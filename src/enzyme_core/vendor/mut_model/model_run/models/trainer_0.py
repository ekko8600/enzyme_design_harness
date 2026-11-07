
import jax
import jax.numpy as jnp
from flax import nnx
import optax



@nnx.jit
def jit_train_step(model, optimizer, batch,  ref_logits_pref, ref_logits_rej, beta, global_loss_weight, rng_key):
    def compute_sequence_log_probs(sequence_logits, target_sequence):
        batch_size, seq_len, _ = sequence_logits.shape
        log_probs = jax.nn.log_softmax(sequence_logits, axis=-1)
        
        batch_indices = jnp.arange(batch_size)[:, None]
        seq_indices = jnp.arange(seq_len)[None, :]
        selected_log_probs = log_probs[batch_indices, seq_indices, target_sequence]
        total_log_probs = jnp.mean(selected_log_probs, axis=1)          ###############修改
        
        return total_log_probs
    
    def extract_target_sequence(sequences_onehot):
        target_sequence = jnp.argmax(sequences_onehot, axis=-1)
        return target_sequence
    
    def loss_fn(model):        
        model_full_out_pref = model(
            batch['preferred_sequences_onehot']
        )
        model_full_out_rej = model(
            batch['rejected_sequences_onehot']
        )
        
        model_out_pref = model_full_out_pref['sequence']
        model_out_rej = model_full_out_rej['sequence']
        
        model_global_pref = model_full_out_pref['global']
        model_global_rej = model_full_out_rej['global']
        
        ref_logprobs_pref = ref_logits_pref['sequence']
        ref_logprobs_rej = ref_logits_rej['sequence']


        target_seq_pref = extract_target_sequence(batch['preferred_sequences_onehot'])
        target_seq_rej = extract_target_sequence(batch['rejected_sequences_onehot'])

        model_logprobs_pref = compute_sequence_log_probs(
            model_out_pref, target_seq_pref
        )
        model_logprobs_rej = compute_sequence_log_probs(
            model_out_rej, target_seq_rej
        )

        model_logratios = model_logprobs_pref - model_logprobs_rej
        ref_logratios = ref_logprobs_pref - ref_logprobs_rej
        logits = beta * (model_logratios - ref_logratios)
        
        score_diff = batch['preferred_scores'] - batch['rejected_scores']
        weights = jax.nn.sigmoid(score_diff)
        dpo_loss = -jnp.mean(jax.nn.log_sigmoid(logits) * weights)
        
        mse_loss_pref = jnp.mean((model_global_pref.squeeze() - batch['preferred_scores']) ** 2)
        mse_loss_rej = jnp.mean((model_global_rej.squeeze() - batch['rejected_scores']) ** 2)
        global_mse_loss = (mse_loss_pref + mse_loss_rej) / 2
        
        total_loss = dpo_loss + global_loss_weight * global_mse_loss
        
        metrics = {
            'total_loss': total_loss,
            'dpo_loss': dpo_loss,
            'global_mse_loss': global_mse_loss,
            'model_logprobs_pref': jnp.mean(model_logprobs_pref),
            'model_logprobs_rej': jnp.mean(model_logprobs_rej),
            'ref_logprobs_pref': jnp.mean(ref_logprobs_pref),
            'ref_logprobs_rej': jnp.mean(ref_logprobs_rej),
            'reward_accuracy': jnp.mean(logits > 0),
            'reward_margin': jnp.mean(model_logratios - ref_logratios),
            'global_pred_pref': jnp.mean(model_global_pref),
            'global_pred_rej': jnp.mean(model_global_rej)
        }
        
        return total_loss, metrics
    
    (loss, metrics), grads = nnx.value_and_grad(loss_fn, has_aux=True)(model)
    
    optimizer.update(grads)
    
    return metrics

@nnx.jit
def jit_evaluate_step(model, batch, ref_logits_pref, ref_logits_rej, beta, global_loss_weight, rng_key):
    def compute_sequence_log_probs(sequence_logits, target_sequence):
        batch_size, seq_len, _ = sequence_logits.shape
        log_probs = jax.nn.log_softmax(sequence_logits, axis=-1)
        
        batch_indices = jnp.arange(batch_size)[:, None]
        seq_indices = jnp.arange(seq_len)[None, :]
        selected_log_probs = log_probs[batch_indices, seq_indices, target_sequence]
        total_log_probs = jnp.sum(selected_log_probs, axis=1)
        
        return total_log_probs
    
    def extract_target_sequence(sequences_onehot):
        target_sequence = jnp.argmax(sequences_onehot, axis=-1)
        return target_sequence
    
    model_full_out_pref = model(batch['preferred_sequences_onehot'])
    model_full_out_rej = model(batch['rejected_sequences_onehot'])
    
    model_out_pref = model_full_out_pref['sequence']
    model_out_rej = model_full_out_rej['sequence']
    model_global_pref = model_full_out_pref['global']
    model_global_rej = model_full_out_rej['global']
    
    ref_logprobs_pref = ref_logits_pref['sequence']
    ref_logprobs_rej = ref_logits_rej['sequence']

    target_seq_pref = extract_target_sequence(batch['preferred_sequences_onehot'])
    target_seq_rej = extract_target_sequence(batch['rejected_sequences_onehot'])
    
    model_logprobs_pref = compute_sequence_log_probs(model_out_pref, target_seq_pref)
    model_logprobs_rej = compute_sequence_log_probs(model_out_rej, target_seq_rej)
    
    model_logratios = model_logprobs_pref - model_logprobs_rej
    ref_logratios = ref_logprobs_pref - ref_logprobs_rej
    logits = beta * (model_logratios - ref_logratios)
    

    score_diff = batch['preferred_scores'] - batch['rejected_scores']
    weights = jax.nn.sigmoid(score_diff)
    dpo_loss = -jnp.mean(jax.nn.log_sigmoid(logits) * weights)
    
    mse_loss_pref = jnp.mean((model_global_pref.squeeze() - batch['preferred_scores']) ** 2)
    mse_loss_rej = jnp.mean((model_global_rej.squeeze() - batch['rejected_scores']) ** 2)
    global_mse_loss = (mse_loss_pref + mse_loss_rej) / 2
    
    total_loss = dpo_loss + global_loss_weight * global_mse_loss
    
    metrics = {
        'total_loss': total_loss,
        'dpo_loss': dpo_loss,
        'global_mse_loss': global_mse_loss,
        'model_logprobs_pref': jnp.mean(model_logprobs_pref),
        'model_logprobs_rej': jnp.mean(model_logprobs_rej),
        'ref_logprobs_pref': jnp.mean(ref_logprobs_pref),
        'ref_logprobs_rej': jnp.mean(ref_logprobs_rej),
        'reward_accuracy': jnp.mean(logits > 0),
        'reward_margin': jnp.mean(model_logratios - ref_logratios),
        'global_pred_pref': jnp.mean(model_global_pref),
        'global_pred_rej': jnp.mean(model_global_rej)
    }
    
    return metrics

class SequenceDPOTrainer:    
    def __init__(self, 
                 model, 
                 beta=0.1,
                 global_loss_weight=1,
                 learning_rate=1e-5,
                 seed=42):

        self.model = model

        self.beta = beta
        self.global_loss_weight = global_loss_weight
        self.optimizer = nnx.Optimizer(model, optax.adam(learning_rate))
        self.training_stats = []
        self.rng_key = jax.random.PRNGKey(seed)
    
    def train_step(self, batch):
        jit_batch = {k: v for k, v in batch.items() 
                    if k not in ['preferred_sequences', 'rejected_sequences']}
        
        ref_logits_pref = {'sequence': batch['preferred_saprot_logits']}
        ref_logits_rej = {'sequence': batch['rejected_saprot_logits']}
        
        metrics = jit_train_step(
            self.model,
            self.optimizer,
            jit_batch,
            ref_logits_pref,
            ref_logits_rej,
            self.beta,
            self.global_loss_weight,
            self.rng_key
        )
        
        self.training_stats.append(metrics)
        
        return metrics
    
    def evaluate(self, eval_data_generator, num_eval_batches=10):
        eval_metrics = []
        
        for _ in range(num_eval_batches):
            batch = eval_data_generator.create_batch()
            jit_batch = {k: v for k, v in batch.items() 
                        if k not in ['preferred_sequences', 'rejected_sequences']}
            
            ref_logits_pref = {'sequence': batch['preferred_saprot_logits']}
            ref_logits_rej = {'sequence': batch['rejected_saprot_logits']}

            metrics = jit_evaluate_step(
                self.model,
                jit_batch,
                ref_logits_pref,
                ref_logits_rej,
                self.beta,
                self.global_loss_weight,
                self.rng_key
            )
            
            eval_metrics.append(metrics)
        
        avg_metrics = {}
        for key in eval_metrics[0].keys():
            values = [m[key] for m in eval_metrics]
            avg_metrics[key] = jnp.mean(jnp.array(values))
            avg_metrics[f'{key}_std'] = jnp.std(jnp.array(values))
        
        return avg_metrics

    
    
    def train(self, 
            train_data_generator, 
            eval_data_generator=None,
            num_epochs=10, 
            steps_per_epoch=100,
            eval_frequency=10,
            save_frequency=100,
            # 早停参数
            patience=5,              # 连续多少个epoch没有改善就停止
            min_delta=0.001,         # 最小改善阈值
            monitor_metric='dpo_loss'): # 监控的指标
        
        global_step = 0
        best_loss = float('inf')
        patience_counter = 0
        epoch_losses = []
        
        for epoch in range(num_epochs):
            print(f"\n=== Epoch {epoch + 1}/{num_epochs} ===")
            epoch_metrics = []
            
            for step in range(steps_per_epoch):
                batch = train_data_generator.create_batch()
                metrics = self.train_step(batch)
                epoch_metrics.append(metrics)
                global_step += 1
            
            epoch_avg_metrics = {}
            for key in epoch_metrics[0].keys():
                values = [m[key] for m in epoch_metrics]
                epoch_avg_metrics[key] = jnp.mean(jnp.array(values))
            
            current_loss = float(epoch_avg_metrics['dpo_loss'])
            epoch_losses.append(current_loss)
            
            print(f"  Avg Loss: {current_loss:.4f}")
            current_metric = float(epoch_avg_metrics[monitor_metric])
            if (best_loss - current_metric) > min_delta:
                best_loss = current_metric
                patience_counter = 0
            else:
                patience_counter += 1
            
            if len(epoch_losses) >= 3:
                recent_losses = epoch_losses[-3:]
                loss_trend = abs(recent_losses[-1] - recent_losses[0]) / 2
                
                if loss_trend < min_delta/10:
                    print(f"损失变化极小 ({loss_trend:.6f}), 可能已收敛")
            
            if patience_counter >= patience and epoch > 20:
                print(f"\n早停触发! {patience} 个epoch内 {monitor_metric} 无显著改善")
                print(f"最佳 {monitor_metric}: {best_loss:.4f}")
                print(f"训练在第 {epoch + 1} 个epoch停止")
                break
        
        print(f"\n训练完成! 总共训练了 {epoch + 1} 个epoch")
        print(f"最终 {monitor_metric}: {current_loss:.4f}")
        print(f"最佳 {monitor_metric}: {best_loss:.4f}")
        
        return {
            'epoch_losses': epoch_losses,
            'best_loss': best_loss,
            'total_epochs': epoch + 1,
            'final_metrics': epoch_avg_metrics,
            'early_stopped': patience_counter >= patience
        }
