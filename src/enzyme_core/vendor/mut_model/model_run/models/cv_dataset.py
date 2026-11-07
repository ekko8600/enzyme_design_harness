import grain.python as grain
import jax
import jax.numpy as jnp
from flax import nnx
from flax import linen as nn
from flax.nnx import bridge
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
import numpy as np
from .dataloder_direct import MySourceSubset


class CrossValidationDataSplit:
    def __init__(self, data_source, cv_scheme: str = "fold_random_5", seed: int = 42):
        self.data_source = data_source
        self.cv_scheme = cv_scheme
        self.seed = seed
        self.n_folds = int(cv_scheme.split('_')[-1]) if cv_scheme.split('_')[-1].isdigit() else 5
        
    def create_cv_folds(self) -> List[Tuple]:
        total_len = len(self.data_source)
        
        if self.cv_scheme.startswith("fold_random"):
            return self._create_random_folds(total_len)
        elif self.cv_scheme.startswith("fold_modulo"):
            return self._create_modulo_folds(total_len)
        elif self.cv_scheme.startswith("fold_contiguous"):
            return self._create_contiguous_folds(total_len)
        else:
            raise ValueError(f"不支持的交叉验证方案: {self.cv_scheme}")
    
    def _create_random_folds(self, total_len: int) -> List[Tuple]:
        np.random.seed(self.seed)
        indices = np.arange(total_len)
        np.random.shuffle(indices)
        
        fold_assignments = np.array([i % self.n_folds for i in range(total_len)])
        np.random.shuffle(fold_assignments)
        
        folds = []
        for test_fold in range(self.n_folds):
            train_idx = (fold_assignments != test_fold).tolist()
            test_idx = (fold_assignments == test_fold).tolist()
            folds.append((train_idx, test_idx, test_fold))
        
        return folds
    
    def _create_modulo_folds(self, total_len: int) -> List[Tuple]:
        folds = []
        for test_fold in range(self.n_folds):
            train_idx = [(i % self.n_folds) != test_fold for i in range(total_len)]
            test_idx = [(i % self.n_folds) == test_fold for i in range(total_len)]
            folds.append((train_idx, test_idx, test_fold))
        
        return folds
    
    def _create_contiguous_folds(self, total_len: int) -> List[Tuple]:
        fold_size = total_len // self.n_folds
        folds = []
        
        for test_fold in range(self.n_folds):
            test_start = test_fold * fold_size
            test_end = (test_fold + 1) * fold_size if test_fold < self.n_folds - 1 else total_len
            
            train_idx = [i < test_start or i >= test_end for i in range(total_len)]
            test_idx = [test_start <= i < test_end for i in range(total_len)]
            folds.append((train_idx, test_idx, test_fold))
        
        return folds


def create_cv_datasets(source_zero, cv_scheme: str = "fold_random_5", seed: int = 42, 
                      batch_size_train: int = 32, batch_size_test: int = 128):
    
    cv_splitter = CrossValidationDataSplit(source_zero, cv_scheme, seed)
    folds = cv_splitter.create_cv_folds()
    
    cv_datasets = []
    
    for fold_idx, (train_idx, test_idx, test_fold) in enumerate(folds):
        print(f"--- 折叠 {fold_idx + 1}/{len(folds)} (测试折叠: {test_fold}) ---")
        print(f"训练样本数: {sum(train_idx)}, 测试样本数: {sum(test_idx)}")
        
        train_indices = [i for i, is_train in enumerate(train_idx) if is_train]
        train_subset = MySourceSubset(source_zero, train_indices)
        train_dataset = (grain.MapDataset.source(train_subset)
                        .shuffle(seed=seed + fold_idx)
                        .batch(batch_size=batch_size_train))
        
        test_indices = [i for i, is_test in enumerate(test_idx) if is_test]
        test_subset = MySourceSubset(source_zero, test_indices)
        test_dataset = (grain.MapDataset.source(test_subset)
                       .batch(batch_size=batch_size_test))
        
        cv_datasets.append({
            'fold': test_fold,
            'train_dataset': train_dataset,
            'test_dataset': test_dataset,
            'train_indices': train_indices,
            'test_indices': test_indices
        })
    
    return cv_datasets

def create_varying_size_datasets(source_zero, 
                                 train_sizes: List[int] = [20, 40, 60, 80, 100, 200],
                                 random_seeds: List[int] = [42, 123, 456, 789, 1024, 2048, 3072, 4096, 5120, 6144],
                                 batch_size_train: int = 32, 
                                 batch_size_test: int = 128):

    total_len = len(source_zero)
    all_datasets = []
    
    for train_size in train_sizes:
        if train_size >= total_len:
            print(f"警告: 训练集大小 {train_size} 大于等于总数据量 {total_len}，跳过此配置")
            continue
            
        for seed in random_seeds:
            print(f"--- 训练集大小: {train_size}, 随机种子: {seed} ---")
            
            # 使用随机种子随机选择训练集索引
            np.random.seed(seed)
            all_indices = np.arange(total_len)
            np.random.shuffle(all_indices)
            
            # 选择前train_size个作为训练集，其余作为测试集
            train_indices = sorted(all_indices[:train_size].tolist())
            test_indices = sorted(all_indices[train_size:].tolist())
            
            val_size = int(0.1*total_len+train_size)
            val_indices = sorted(all_indices[train_size:val_size].tolist())
            
            print(f"训练样本数: {len(train_indices)}, 测试样本数: {len(test_indices)}")
            
            # 创建训练集
            train_subset = MySourceSubset(source_zero, train_indices)
            train_dataset = (grain.MapDataset.source(train_subset)
                            .shuffle(seed=seed)
                            .batch(batch_size=batch_size_train))
            
            # 创建测试集
            test_subset = MySourceSubset(source_zero, test_indices)
            test_dataset = (grain.MapDataset.source(test_subset)
                           .batch(batch_size=batch_size_test))
            
            # 创建验证集
            val_subset = MySourceSubset(source_zero, val_indices)
            val_dataset = (grain.MapDataset.source(val_subset)
                           .batch(batch_size=batch_size_test))
            
            all_datasets.append({
                'train_size': train_size,
                'seed': seed,
                'train_dataset': train_dataset,
                'test_dataset': test_dataset,
                'train_indices': train_indices,
                'test_indices': test_indices,
                'val_dataset': val_dataset
            })
    
    print(f"\n总共创建了 {len(all_datasets)} 个数据集划分")
    return all_datasets
