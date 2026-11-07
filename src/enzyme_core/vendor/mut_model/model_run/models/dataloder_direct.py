
import numpy as np
import pandas as pd
import grain.python as grain
import jax
import jax.numpy as jnp 

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

class MySource_seqs(grain.RandomAccessDataSource):
    def __init__(self, table, y_name, vocab_size=20):
        
        self.df = pd.read_csv(table, sep=',')
        self.Ys_score = pd.DataFrame(self.df[y_name])
        self.Ys_score = self._normalize_to_range(self.Ys_score, target_min=-1, target_max=1)
        self.Ys = jnp.array(self.Ys_score)
        
        self.sequences = self.df['mutated_sequence'].tolist()
        self.vocab_size = vocab_size
        self.amino_acids = 'ACDEFGHIKLMNPQRSTVWY'
        self.aa_to_idx = {aa: i for i, aa in enumerate(self.amino_acids)}
        
    def _normalize_to_range(self, data, target_min=-1, target_max=1):
        data_min = data.min().iloc[0]
        data_max = data.max().iloc[0]
        if data_max == data_min:
            print("警告: 数据范围为0，无法归一化")
            return data

        normalized_data = (data - data_min) / (data_max - data_min) * (target_max - target_min) + target_min
        
        return normalized_data
    
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
            'idx': idx
        }
    
    def __len__(self):
        return len(self.Ys)
    
    def get_sequence_by_idx(self, idx):
        return self.sequences[idx]
    
    def get_score_by_idx(self, idx):
        return float(self.Ys[idx])
    
