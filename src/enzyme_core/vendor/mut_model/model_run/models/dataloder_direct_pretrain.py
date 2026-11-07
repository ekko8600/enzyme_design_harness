
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
        
        # 支持单个或多个y_name
        if isinstance(y_name, str):
            # 单个y_name的情况
            self.y_names = [y_name]
            self.multi_y = False
        elif isinstance(y_name, list):
            # 多个y_name的情况
            self.y_names = y_name
            self.multi_y = True
        else:
            raise ValueError("y_name必须是字符串或字符串列表")
        
        # 为每个y_name准备归一化后的数据
        self.Ys_list = []
        for yn in self.y_names:
            Ys_score = pd.DataFrame(self.df[yn])
            Ys_score = self._normalize_to_range(Ys_score, target_min=-1, target_max=1)
            self.Ys_list.append(jnp.array(Ys_score))
        
        # 如果只有一个y_name，保持向后兼容
        if not self.multi_y:
            self.Ys = self.Ys_list[0]
        
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
        sequence = self.sequences[idx]
        sequence_onehot = self.sequence_to_onehot(sequence)
        
        result = {
            'sequence_onehot': sequence_onehot,
            'sequence': sequence,
            'idx': idx
        }
        
        # 根据是否多y_name返回不同的数据结构
        if self.multi_y:
            # 多y_name模式：返回y_list
            result['y_list'] = [self.Ys_list[i][idx] for i in range(len(self.y_names))]
            # 为了向后兼容，也提供单个y（第一个y_name）
            result['y'] = self.Ys_list[0][idx]
        else:
            # 单y_name模式：返回y
            result['y'] = self.Ys[idx]
        
        return result
    
    def __len__(self):
        return len(self.Ys_list[0]) if self.multi_y else len(self.Ys)
    
    def get_sequence_by_idx(self, idx):
        return self.sequences[idx]
    
    def get_score_by_idx(self, idx):
        if self.multi_y:
            return [float(self.Ys_list[i][idx][0]) for i in range(len(self.y_names))]
        else:
            return float(self.Ys[idx][0])
    
