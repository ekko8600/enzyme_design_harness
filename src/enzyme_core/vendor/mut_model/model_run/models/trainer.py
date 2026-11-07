
import jax
import jax.numpy as jnp
from flax import nnx
import optax


@nnx.jit
def jit_train_step(model, optimizer, batch, ref_logits_pref, ref_logits_rej, beta, global_loss_weight, rng_key, epoch_progress=0.0):
    
    def compute_sequence_log_probs(sequence_logits, target_sequence):
        batch_size, seq_len, _ = sequence_logits.shape
        log_probs = jax.nn.log_softmax(sequence_logits, axis=-1)
        
        batch_indices = jnp.arange(batch_size)[:, None]
        seq_indices = jnp.arange(seq_len)[None, :]
        selected_log_probs = log_probs[batch_indices, seq_indices, target_sequence]
        total_log_probs = jnp.sum(selected_log_probs, axis=1)
        
        return total_log_probs
    
    def extract_target_sequence(sequences_onehot):
        return jnp.argmax(sequences_onehot, axis=-1)
    
    def loss_fn(model):        
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
        
        # ============ 优化1: Top-k Focused Weighting ============
        # 计算样本在整体分布中的位置（基于true score）
        all_true_scores = jnp.concatenate([batch['preferred_scores'], batch['rejected_scores']])
        score_percentile_pref = jnp.mean(
            (batch['preferred_scores'][:, None] > all_true_scores[None, :]).astype(jnp.float32), 
            axis=1
        )
        score_percentile_rej = jnp.mean(
            (batch['rejected_scores'][:, None] > all_true_scores[None, :]).astype(jnp.float32), 
            axis=1
        )
        
        # Top区域样本获得更高权重（指数增长）
        # 例如：top10%样本权重是均值样本的3-5倍
        topk_weight_pref = 1.0 + 4.0 * jnp.exp(3.0 * (score_percentile_pref - 0.5))
        topk_weight_rej = 1.0 + 4.0 * jnp.exp(3.0 * (score_percentile_rej - 0.5))
        topk_weight = (topk_weight_pref + topk_weight_rej) / 2
        
        # ============ 优化2: Focal DPO Loss ============
        # 对难样本（logits接近0，即模型不确定）增加权重
        gamma = 2.0  # focal loss的gamma参数
        prob_correct = jax.nn.sigmoid(logits)
        focal_weight = (1 - prob_correct) ** gamma
        
        # ============ 优化3: Adaptive Score-based Weighting ============
        score_diff = batch['preferred_scores'] - batch['rejected_scores']
        # 使用更陡峭的sigmoid，让高score差异的样本更突出
        score_weights = jax.nn.sigmoid(score_diff * 3.0)
        
        # 结合所有权重
        pair_weights = batch.get('pair_weights', jnp.ones_like(score_weights))
        combined_weights = score_weights * pair_weights * topk_weight * focal_weight
        combined_weights = combined_weights / (jnp.mean(combined_weights) + 1e-8)
        
        # DPO Loss with enhanced weighting
        dpo_loss = -jnp.mean(jax.nn.log_sigmoid(logits) * combined_weights)
        
        # Hard Negative Penalty
        wrong_order_mask = (logits < 0).astype(jnp.float32)
        penalty_loss = -jnp.mean(jax.nn.log_sigmoid(logits) * wrong_order_mask * 3.0)
        dpo_loss = dpo_loss + 0.15 * penalty_loss
        
        # ============ 优化4: Percentile-aware MSE Loss ============
        # 对高分样本使用更严格的MSE（降低容忍度）
        score_range = jnp.max(all_true_scores) - jnp.min(all_true_scores)
        
        # Top区域使用更小的delta（更严格的约束）
        adaptive_delta_pref = jnp.where(
            score_percentile_pref > 0.8,  # top 20%
            jnp.maximum(score_range * 0.05, 0.05),  # 更小的delta
            jnp.maximum(score_range * 0.1, 0.1)
        )
        adaptive_delta_rej = jnp.where(
            score_percentile_rej > 0.8,
            jnp.maximum(score_range * 0.05, 0.05),
            jnp.maximum(score_range * 0.1, 0.1)
        )
        
        def weighted_huber_loss(pred, target, delta, weight):
            diff = pred - target
            abs_diff = jnp.abs(diff)
            quadratic = 0.5 * diff ** 2
            linear = delta * (abs_diff - 0.5 * delta)
            loss = jnp.where(abs_diff <= delta, quadratic, linear)
            return jnp.mean(loss * weight)
        
        mse_loss_pref = weighted_huber_loss(
            model_global_pref.squeeze(), 
            batch['preferred_scores'], 
            adaptive_delta_pref,
            topk_weight_pref
        )
        mse_loss_rej = weighted_huber_loss(
            model_global_rej.squeeze(), 
            batch['rejected_scores'], 
            adaptive_delta_rej,
            topk_weight_rej
        )
        global_mse_loss = (mse_loss_pref + mse_loss_rej) / 2
        
        # ============ 优化5: Adaptive Margin Ranking Loss ============
        # Margin根据真实score差异动态调整
        true_score_diff = jnp.abs(batch['preferred_scores'] - batch['rejected_scores'])
        normalized_diff = true_score_diff / (score_range + 1e-6)
        # Margin范围：0.1 ~ 0.5
        adaptive_margin = 0.1 + 0.4 * normalized_diff
        
        pred_diff = model_global_pref.squeeze() - model_global_rej.squeeze()
        ranking_loss = jnp.mean(
            jnp.maximum(0, adaptive_margin - pred_diff) * combined_weights
        )
        
        # ============ 优化6: Top-k Calibration Loss ============
        # 专门确保top-k区域的相对顺序正确
        topk_mask_pref = (score_percentile_pref > 0.9).astype(jnp.float32)  # top 10%
        topk_mask_rej = (score_percentile_rej > 0.9).astype(jnp.float32)
        
        # 对于top-k样本，预测值应该高于全局均值 + 一定margin
        all_preds = jnp.concatenate([model_global_pref.squeeze(), model_global_rej.squeeze()])
        pred_mean = jnp.mean(all_preds)
        topk_calibration_loss = (
            jnp.mean(jnp.maximum(0, pred_mean + 0.3 - model_global_pref.squeeze()) * topk_mask_pref) +
            jnp.mean(jnp.maximum(0, pred_mean + 0.3 - model_global_rej.squeeze()) * topk_mask_rej)
        )
        
        # ============ 优化7: Variance Regularization (改进版) ============
        pred_std = jnp.std(all_preds) + 1e-6
        target_std = jnp.std(all_true_scores) + 1e-6
        
        # 不仅匹配std，还要匹配分位数
        pred_sorted = jnp.sort(all_preds)
        target_sorted = jnp.sort(all_true_scores)
        n_samples = pred_sorted.shape[0]
        quantile_indices = jnp.array([
            jnp.clip(int(0.1 * n_samples), 0, n_samples - 1),
            jnp.clip(int(0.5 * n_samples), 0, n_samples - 1),
            jnp.clip(int(0.9 * n_samples), 0, n_samples - 1)
        ])
        quantile_loss = jnp.mean(jnp.abs(
            pred_sorted[quantile_indices] - target_sorted[quantile_indices]
        ))
        
        variance_loss = (jnp.abs(pred_std - target_std) / target_std) + 0.1 * quantile_loss
        
        # ============ 优化8: Curriculum Learning ============
        # 早期训练：更多关注简单样本（score差异大）和MSE
        # 后期训练：更多关注困难样本和DPO排序
        curriculum_factor = jnp.clip(epoch_progress, 0.0, 1.0)
        
        # 早期MSE权重高，后期DPO权重高
        curriculum_mse_weight = 1.5 - 0.5 * curriculum_factor
        curriculum_dpo_weight = 0.5 + 0.5 * curriculum_factor
        
        # Dynamic weighting
        dpo_scale = jax.lax.stop_gradient(jnp.maximum(jnp.abs(dpo_loss), 1e-6))
        mse_scale = jax.lax.stop_gradient(jnp.maximum(jnp.abs(global_mse_loss), 1e-6))
        dynamic_mse_weight = global_loss_weight * (dpo_scale / (mse_scale + 1e-6)) * curriculum_mse_weight
        
        # Final loss composition with curriculum
        total_loss = (
            curriculum_dpo_weight * dpo_loss + 
            dynamic_mse_weight * global_mse_loss + 
            4.0 * ranking_loss +  # ranking保持重要
            2.0 * variance_loss + 
            3.0 * topk_calibration_loss  # top-k校准是关键
        )
        
        metrics = {
            'total_loss': total_loss,
            'dpo_loss': dpo_loss,
            'global_mse_loss': global_mse_loss,
            'ranking_loss': ranking_loss,
            'variance_loss': variance_loss,
            'topk_calibration_loss': topk_calibration_loss,
            'quantile_loss': quantile_loss,
            'pred_std': pred_std,
            'target_std': target_std,
            'avg_topk_weight': jnp.mean(topk_weight),
            'avg_focal_weight': jnp.mean(focal_weight),
            'avg_adaptive_margin': jnp.mean(adaptive_margin),
            'model_logprobs_pref': jnp.mean(model_logprobs_pref),
            'model_logprobs_rej': jnp.mean(model_logprobs_rej),
            'reward_accuracy': jnp.mean(logits > 0),
            'reward_margin': jnp.mean(model_logratios - ref_logratios),
            'global_pred_pref': jnp.mean(model_global_pref),
            'global_pred_rej': jnp.mean(model_global_rej),
            'topk_pred_mean': jnp.sum(
                model_global_pref.squeeze() * topk_mask_pref + 
                model_global_rej.squeeze() * topk_mask_rej
            ) / (jnp.sum(topk_mask_pref) + jnp.sum(topk_mask_rej) + 1e-6),
            'curriculum_factor': curriculum_factor,
            'dynamic_mse_weight': dynamic_mse_weight,
            'adaptive_delta': jnp.mean(adaptive_delta_pref)
        }
        
        return total_loss, metrics
    
    (loss, metrics), grads = nnx.value_and_grad(loss_fn, has_aux=True)(model)
    optimizer.update(grads)
    
    return metrics


@nnx.jit
def jit_evaluate_step(model, batch, ref_logits_pref, ref_logits_rej, beta, global_loss_weight, rng_key):
    """评估步骤 - 使用简化的loss计算"""
    
    def compute_sequence_log_probs(sequence_logits, target_sequence):
        batch_size, seq_len, _ = sequence_logits.shape
        log_probs = jax.nn.log_softmax(sequence_logits, axis=-1)
        
        batch_indices = jnp.arange(batch_size)[:, None]
        seq_indices = jnp.arange(seq_len)[None, :]
        selected_log_probs = log_probs[batch_indices, seq_indices, target_sequence]
        total_log_probs = jnp.sum(selected_log_probs, axis=1)
        
        return total_log_probs
    
    def extract_target_sequence(sequences_onehot):
        return jnp.argmax(sequences_onehot, axis=-1)
    
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
    
    # 计算top-k相关指标
    all_true_scores = jnp.concatenate([batch['preferred_scores'], batch['rejected_scores']])
    all_preds = jnp.concatenate([model_global_pref.squeeze(), model_global_rej.squeeze()])
    score_percentile_pref = jnp.mean(
        (batch['preferred_scores'][:, None] > all_true_scores[None, :]).astype(jnp.float32), 
        axis=1
    )
    score_percentile_rej = jnp.mean(
        (batch['rejected_scores'][:, None] > all_true_scores[None, :]).astype(jnp.float32), 
        axis=1
    )
    topk_mask_pref = (score_percentile_pref > 0.9).astype(jnp.float32)
    topk_mask_rej = (score_percentile_rej > 0.9).astype(jnp.float32)
    
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
        'global_pred_rej': jnp.mean(model_global_rej),
        'pred_std': jnp.std(all_preds),
        'target_std': jnp.std(all_true_scores),
        'topk_pred_mean': jnp.sum(
            model_global_pref.squeeze() * topk_mask_pref + 
            model_global_rej.squeeze() * topk_mask_rej
        ) / (jnp.sum(topk_mask_pref) + jnp.sum(topk_mask_rej) + 1e-6),
    }
    
    return metrics


class SequenceDPOTrainer:    
    
    def __init__(self, 
                 model, 
                 beta=0.1,
                 global_loss_weight=1.0,
                 learning_rate=1e-5,
                 warmup_epochs=5,
                 total_epochs=80,
                 seed=42):

        self.model = model
        self.beta = beta
        self.global_loss_weight = global_loss_weight
        self.warmup_epochs = warmup_epochs
        self.total_epochs_planned = total_epochs
        self.current_epoch = 0
        
        # 使用cosine schedule + warmup（小样本更稳定）
        schedule = optax.warmup_cosine_decay_schedule(
            init_value=learning_rate * 0.1,
            peak_value=learning_rate,
            warmup_steps=warmup_epochs * 50,  # 假设每epoch 50 steps
            decay_steps=total_epochs * 50,
            end_value=learning_rate * 0.01
        )
        
        self.optimizer = nnx.Optimizer(model, optax.chain(
            optax.clip_by_global_norm(1.0),
            optax.adam(schedule)
        ))
        
        self.training_stats = []
        self.rng_key = jax.random.PRNGKey(seed)
    
    def train_step(self, batch):
        jit_batch = {k: v for k, v in batch.items() 
                    if k not in ['preferred_sequences', 'rejected_sequences']}
        
        ref_logits_pref = {'sequence': batch['preferred_saprot_logits']}
        ref_logits_rej = {'sequence': batch['rejected_saprot_logits']}
        
        # 计算epoch进度用于curriculum learning
        epoch_progress = self.current_epoch / max(self.total_epochs_planned, 1)
        
        metrics = jit_train_step(
            self.model,
            self.optimizer,
            jit_batch,
            ref_logits_pref,
            ref_logits_rej,
            self.beta,
            self.global_loss_weight,
            self.rng_key,
            epoch_progress
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
              steps_per_epoch=50,
              eval_frequency=10,
              save_frequency=100,
              patience=5,
              min_delta=0.001,
              monitor_metric='total_loss'):
        
        self.total_epochs_planned = num_epochs
        global_step = 0
        best_loss = float('inf')
        patience_counter = 0
        epoch_losses = []
        
        for epoch in range(num_epochs):
            self.current_epoch = epoch
            print(f"\n=== Epoch {epoch + 1}/{num_epochs} (Progress: {epoch/max(num_epochs,1):.1%}) ===")
            epoch_metrics = []
            
            for step in range(steps_per_epoch):
                batch = train_data_generator.create_batch()
                metrics = self.train_step(batch)
                epoch_metrics.append(metrics)
                global_step += 1
            
            # 计算epoch平均指标
            epoch_avg_metrics = {}
            for key in epoch_metrics[0].keys():
                values = [m[key] for m in epoch_metrics]
                epoch_avg_metrics[key] = jnp.mean(jnp.array(values))
            
            current_loss = float(epoch_avg_metrics[monitor_metric])
            epoch_losses.append(current_loss)
            
            # 打印增强的关键指标
            print(f"  📊 Total Loss: {epoch_avg_metrics['total_loss']:.4f}")
            print(f"  📈 DPO: {epoch_avg_metrics['dpo_loss']:.4f} | MSE: {epoch_avg_metrics['global_mse_loss']:.4f} | Ranking: {epoch_avg_metrics['ranking_loss']:.4f}")
            print(f"  🎯 Top-k Calib: {epoch_avg_metrics['topk_calibration_loss']:.4f} | Variance: {epoch_avg_metrics['variance_loss']:.4f}")
            print(f"  ✅ Reward Acc: {epoch_avg_metrics['reward_accuracy']:.4f} | Margin: {epoch_avg_metrics['reward_margin']:.4f}")
            print(f"  📏 Pred Std: {epoch_avg_metrics['pred_std']:.4f} | Target Std: {epoch_avg_metrics['target_std']:.4f}")
            print(f"  🔝 Top-k Pred: {epoch_avg_metrics['topk_pred_mean']:.4f} | Avg Pred: {(epoch_avg_metrics['global_pred_pref']+epoch_avg_metrics['global_pred_rej'])/2:.4f}")
            print(f"  ⚖️ Top-k Weight: {epoch_avg_metrics['avg_topk_weight']:.2f}x | Focal Weight: {epoch_avg_metrics['avg_focal_weight']:.3f}")
            print(f"  📚 Curriculum: {epoch_avg_metrics['curriculum_factor']:.2f} | Dynamic MSE Weight: {epoch_avg_metrics['dynamic_mse_weight']:.4f}")
            
            # Early stopping检查
            if (best_loss - current_loss) > min_delta:
                best_loss = current_loss
                patience_counter = 0
                print(f"  ⭐ 新的最佳 {monitor_metric}: {best_loss:.4f}")
            else:
                patience_counter += 1
            
            # 检查损失变化趋势
            if len(epoch_losses) >= 3:
                recent_losses = epoch_losses[-3:]
                loss_trend = abs(recent_losses[-1] - recent_losses[0]) / 2
                
                if loss_trend < min_delta/10:
                    print(f"  ⚠️ 损失变化极小 ({loss_trend:.6f}), 可能已收敛")
            
            if patience_counter >= patience and epoch > self.warmup_epochs:
                print(f"\n⏹️ 早停触发! {patience} 个epoch内 {monitor_metric} 无显著改善")
                print(f"最佳 {monitor_metric}: {best_loss:.4f}")
                print(f"训练在第 {epoch + 1} 个epoch停止")
                break
        
        print(f"\n✅ 训练完成! 总共训练了 {epoch + 1} 个epoch")
        print(f"最终 {monitor_metric}: {current_loss:.4f}")
        print(f"最佳 {monitor_metric}: {best_loss:.4f}")
        
        return {
            'epoch_losses': epoch_losses,
            'best_loss': best_loss,
            'total_epochs': epoch + 1,
            'final_metrics': epoch_avg_metrics,
            'early_stopped': patience_counter >= patience
        }
