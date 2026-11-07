import jax
import jax.numpy as jnp
import numpy as np

class PreferenceDataGenerator:    
    def __init__(self, source_with_sequences, vocab_size=20):
        self.source = source_with_sequences
        self.df = source_with_sequences.df
        self.y_name = 'DMS_score'     #####################################
        self.vocab_size = vocab_size
        self.features_cache = {}
        self.preference_pairs = None
    
    def get_features(self, idx):
        if idx not in self.features_cache:
            data = self.source[idx]
            self.features_cache[idx] = data
        return self.features_cache[idx]
    
    def create_preference_pairs(self, n_pairs=10000, strategy='threshold', percentile=70):
        preference_pairs = []
        
        if strategy == 'threshold':
            threshold = np.percentile(self.df[self.y_name], percentile)
            
            temp_df = self.df.reset_index(drop=True)
            high_indices = temp_df[temp_df[self.y_name] >= threshold].index.tolist()
            low_indices = temp_df[temp_df[self.y_name] < threshold].index.tolist()

            if len(high_indices) == 0 or len(low_indices) == 0:
                print("Warning: High or Low group is empty. Cannot create pairs.")
                self.preference_pairs = []
                return []
            
            np.random.shuffle(high_indices)
            np.random.shuffle(low_indices)

            # 平衡采样：确保两组都被充分利用
            min_pairs_needed = max(len(high_indices), len(low_indices))
            total_pairs = max(n_pairs, min_pairs_needed)
            
            # 计算不平衡比率，用于后续加权
            imbalance_ratio = max(len(high_indices), len(low_indices)) / min(len(high_indices), len(low_indices))
            
            print(f"High set size: {len(high_indices)}, Low set size: {len(low_indices)}")
            print(f"Imbalance ratio: {imbalance_ratio:.2f}")
            print(f"Ensuring full coverage (min pairs: {min_pairs_needed}). Total pairs: {total_pairs}")

            for i in range(total_pairs):
                if i < min_pairs_needed:
                    high_idx = high_indices[i % len(high_indices)]
                    low_idx = low_indices[i % len(low_indices)]
                else:
                    high_idx = np.random.choice(high_indices)
                    low_idx = np.random.choice(low_indices)

                high_data = self.get_features(high_idx)
                low_data = self.get_features(low_idx)
                
                # 计算对的重复次数（用于平衡采样权重）
                high_repeat_count = total_pairs // len(high_indices) + 1
                low_repeat_count = total_pairs // len(low_indices) + 1
                pair_weight = 1.0 / max(high_repeat_count, low_repeat_count)
                
                preference_pairs.append({
                    'preferred_score': high_data['y'],
                    'rejected_score': low_data['y'],
                    'preferred_idx': high_idx,
                    'rejected_idx': low_idx,
                    'preferred_sequence': high_data['sequence'],
                    'rejected_sequence': low_data['sequence'],
                    'preferred_sequences_onehot': high_data['sequence_onehot'],
                    'rejected_sequences_onehot': low_data['sequence_onehot'],
                    'preferred_saprot_logits': high_data['saprot_logits'],
                    'rejected_saprot_logits': low_data['saprot_logits'],
                    'pair_weight': pair_weight  # 添加对权重
                })
                
        print(f"生成了 {len(preference_pairs)} 个偏好对")
        self.preference_pairs = preference_pairs
        return preference_pairs
    
    def create_batch(self, preference_pairs=None, batch_size=32):
        if preference_pairs is None:
            preference_pairs = self.preference_pairs
        
        if not preference_pairs:
            raise ValueError("没有可用的偏好对数据")
        
        batch_pairs = np.random.choice(preference_pairs, size=batch_size, replace=True)
        
        batch = {}
        batch['preferred_scores'] = jnp.array([pair['preferred_score'] for pair in batch_pairs])
        batch['rejected_scores'] = jnp.array([pair['rejected_score'] for pair in batch_pairs])
        batch['preferred_sequences'] = [pair['preferred_sequence'] for pair in batch_pairs]
        batch['rejected_sequences'] = [pair['rejected_sequence'] for pair in batch_pairs]
        
        batch['preferred_sequences_onehot'] = jnp.stack([
            pair['preferred_sequences_onehot'] for pair in batch_pairs
        ])
        batch['rejected_sequences_onehot'] = jnp.stack([
            pair['rejected_sequences_onehot'] for pair in batch_pairs
        ])
        batch['preferred_saprot_logits'] = jnp.stack([
            pair['preferred_saprot_logits'] for pair in batch_pairs
        ])
        batch['rejected_saprot_logits'] = jnp.stack([
            pair['rejected_saprot_logits'] for pair in batch_pairs
        ])
        
        # 添加对权重（如果存在）
        if 'pair_weight' in batch_pairs[0]:
            batch['pair_weights'] = jnp.array([pair['pair_weight'] for pair in batch_pairs])
        else:
            batch['pair_weights'] = jnp.ones(batch_size)
        
        return batch
