import os
import torch
import numpy as np
import pandas as pd

import grain.python as grain
import jax
import jax.numpy as jnp 
import jax.dlpack
from torch.utils.dlpack import to_dlpack
from transformers import EsmTokenizer, EsmForMaskedLM
from saprot_model.foldseek_util_mut import get_struc_seq
import pickle
import torch.nn.functional as F

class MySourceSubset(grain.RandomAccessDataSource):
    def __init__(self, parent_source, indices):
        self.parent_source = parent_source
        self.indices = indices
        
    def __getitem__(self, idx):
        parent_idx = self.indices[idx]
        return self.parent_source[parent_idx]
    
    def __len__(self):
        return len(self.indices)
    
    @property
    def df(self):
        return self.parent_source.df.iloc[self.indices]
    
def saprot_log_probs_pytorch(sequence_logits, target_sequence):

    batch_size, seq_len, vocab_size = sequence_logits.shape
    log_probs = F.log_softmax(sequence_logits, dim=-1)
    batch_indices = torch.arange(batch_size, device=sequence_logits.device).unsqueeze(1)  # (batch_size, 1)
    seq_indices = torch.arange(seq_len, device=sequence_logits.device).unsqueeze(0)      # (1, seq_len)
    selected_log_probs = log_probs[batch_indices, seq_indices, target_sequence]
    total_log_probs = torch.mean(selected_log_probs, dim=1)
    
    return total_log_probs

class MySourceWithSequences(grain.RandomAccessDataSource):
    def __init__(self, table, y_name,table_name,pdb_path=None, vocab_size=20, precompute_saprot=True, cache_file=None):
        self.df = pd.read_csv(table, sep=',')
        self.Ys_score = pd.DataFrame(self.df[y_name])
        self.Ys_score = self._normalize_to_range(self.Ys_score, target_min=-1, target_max=1)
        self.Ys = jnp.array(self.Ys_score)
        
        self.sequences = self.df['mutated_sequence'].tolist()
        self.vocab_size = vocab_size
        self.amino_acids = 'ACDEFGHIKLMNPQRSTVWY'
        self.aa_to_idx = {aa: i for i, aa in enumerate(self.amino_acids)}
        
        self.saprot_logits_cache = {}
        self.cache_file = cache_file or f"./result/cache_file2/saprot_logits_cache_{table_name}_{len(self.sequences)}.pkl"
        
        self.pdb_path = pdb_path
        
        if precompute_saprot:
            self._precompute_saprot_logits(self.pdb_path)
            
    def _normalize_to_range(self, data, target_min=-1, target_max=1):
        data_min = data.min().iloc[0]
        data_max = data.max().iloc[0]
        if data_max == data_min:
            print("警告: 数据范围为0，无法归一化")
            return data

        normalized_data = (data - data_min) / (data_max - data_min) * (target_max - target_min) + target_min
        
        return normalized_data
    
    def _precompute_saprot_logits(self,pdb_path):
        
        if os.path.exists(self.cache_file):
            print(f"加载已有缓存: {self.cache_file}")
            try:
                with open(self.cache_file, 'rb') as f:
                    self.saprot_logits_cache = pickle.load(f)
                print(f"成功加载 {len(self.saprot_logits_cache)} 个缓存的logits")
                return
            except Exception as e:
                print(f"加载缓存失败: {e}，将重新计算")
        
        model_path = "./SaProt_650M_AF2"
        tokenizer = EsmTokenizer.from_pretrained(model_path)
        model_saprot = EsmForMaskedLM.from_pretrained(model_path)
        device = "cuda"
        model_saprot.to(device)
        model_saprot.eval()
        
        batch_size = 64
        total_sequences = len(self.sequences)
        
        for start_idx in range(0, total_sequences, batch_size):
            end_idx = min(start_idx + batch_size, total_sequences)
            
            print(f"处理序列 {start_idx+1}-{end_idx}/{total_sequences}")
            
            for seq_idx in range(start_idx, end_idx):
                if seq_idx in self.saprot_logits_cache:
                    continue
                
                sequence = self.sequences[seq_idx]
                logits = self._compute_single_saprot_logits(
                    sequence, pdb_path, model_saprot, tokenizer, device
                )
                self.saprot_logits_cache[seq_idx] = logits
            torch.cuda.empty_cache()
            if (end_idx) % (batch_size * 10) == 0:
                self._save_cache()
        
        self._save_cache() 
        del model_saprot
        del tokenizer
        torch.cuda.empty_cache()
        print(f"SaProt logits预计算完成! 缓存了 {len(self.saprot_logits_cache)} 个序列的logits")
    
    def _compute_single_saprot_logits(self, sequence,pdb_path, model_saprot, tokenizer, device):
        parsed_seqs = get_struc_seq("bin/foldseek", pdb_path, sequence, ["A"], plddt_mask=False)["A"]
        seq, foldseek_seq, combined_seq = parsed_seqs
        
        inputs = tokenizer(combined_seq, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        
        with torch.no_grad():
            outputs = model_saprot(**inputs)
            logits = outputs.logits[:, 1:-1]  # shape: (1, seq_len, vocab_size)
            target_seq = inputs['input_ids'][:, 1:-1]
            result_log_probs = saprot_log_probs_pytorch(logits,target_seq)
            logits_jax = jax.dlpack.from_dlpack(to_dlpack(result_log_probs.contiguous()))
            logits_cpu = jnp.array(logits_jax[0])
            
            del outputs
            del inputs
            del logits
            del result_log_probs
            del logits_jax
            del target_seq
            torch.cuda.empty_cache()        
        return logits_cpu
    
    def _save_cache(self):
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self.saprot_logits_cache, f)
            print(f"缓存已保存到 {self.cache_file}")
        except Exception as e:
            print(f"保存缓存失败: {e}")
    
    def get_saprot_logits(self, idx):
        if idx in self.saprot_logits_cache:
            return self.saprot_logits_cache[idx]
        else:
            print(f"警告: 索引 {idx} 的logits未找到，返回零填充")
            seq_len = len(self.sequences[idx])
            return jnp.zeros((seq_len, self.vocab_size))
    
    def get_saprot_logits_batch(self, indices):
        batch_logits = []
        for idx in indices:
            logits = self.get_saprot_logits(idx)
            batch_logits.append(logits)
        
        max_len = max([logits.shape[0] for logits in batch_logits])
        padded_logits = []
        for logits in batch_logits:
            if logits.shape[0] < max_len:
                padding = jnp.zeros((max_len - logits.shape[0], self.vocab_size))
                padded_logits.append(jnp.concatenate([logits, padding], axis=0))
            else:
                padded_logits.append(logits)
        
        return jnp.stack(padded_logits, axis=0)
    
    def sequence_to_onehot(self, sequence):
        seq_len = len(sequence)
        onehot = np.zeros((seq_len, self.vocab_size))
        for i, aa in enumerate(sequence):
            if aa in self.aa_to_idx:
                onehot[i, self.aa_to_idx[aa]] = 1.0        
        return jnp.array(onehot)
    
    def __getitem__(self, idx):
        y = self.Ys[idx]
        sequence = self.sequences[idx]
        sequence_onehot = self.sequence_to_onehot(sequence)
        
        return {
            'y': y,
            'sequence_onehot': sequence_onehot,
            'sequence': sequence,
            'idx': idx,
            'saprot_logits': self.get_saprot_logits(idx)
        }
    
    def __len__(self):
        return len(self.Ys)
    
    def create_train_test_split(self, train_ratio=0.8, seed=42):
        np.random.seed(seed)
        total_len = len(self)
        indices = np.arange(total_len)
        np.random.shuffle(indices)
        
        train_size = int(total_len * train_ratio)
        train_indices = indices[:train_size]
        test_indices = indices[train_size:]
        
        train_source = MySourceSubset(self, train_indices)
        test_source = MySourceSubset(self, test_indices)
        
        return train_source, test_source
