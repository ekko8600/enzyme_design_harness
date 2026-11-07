
import jax
from flax import nnx
from flax import linen as nn
from flax.nnx import bridge
from jax import numpy as jnp
from typing import *
from models.model_gated_linear_rnn import GatedLinearRNN
from jax.nn import gelu, elu
from .attentions import MultiHeadSelfAttention, MultiHeadCrossAttention
from flax.linen import Module, compact
from flax.linen import initializers
from collections.abc import Callable
from jax import lax

class Dense(Module):
    features: int
    kernel_init: Callable = initializers.lecun_normal()
    bias_init: Callable = initializers.zeros_init()
    use_bias: bool = True

    @compact
    def __call__(self, inputs):
        kernel = self.param(
            'kernel', self.kernel_init, (inputs.shape[-1], self.features)
        )
        y = lax.dot_general(
            inputs,
            kernel,
            (((inputs.ndim - 1,), (0,)), ((), ())),
        )
        if self.use_bias:
            bias = self.param('bias', self.bias_init, (self.features,))
            y = y + bias
        return y

class ProteinSequenceEmbedding(Module):
    vocab_size: int = 20  # 20种标准氨基酸
    embed_dim: int = 1280  # embedding维度
    max_seq_len: int = 1024  # 最大序列长度
    use_positional_encoding: bool = True
    #dropout_rate: float = 0.1
    
    def setup(self):
        self.amino_acid_embedding = nn.Embed(
            num_embeddings=self.vocab_size,
            features=self.embed_dim,
            embedding_init=initializers.normal(stddev=0.02)
        )
        
        if self.use_positional_encoding:
            self.position_embedding = nn.Embed(
                num_embeddings=self.max_seq_len,
                features=self.embed_dim,
                embedding_init=initializers.normal(stddev=0.02)
            )
        
        self.layer_norm = nn.LayerNorm()
        # Dropout
        #self.dropout = nn.Dropout(rate=self.dropout_rate)
        
        self.projection = nn.Dense(
            features=self.embed_dim,
            kernel_init=initializers.xavier_uniform()
        )
        
    def __call__(self, sequence_onehot, training=True):
        if sequence_onehot.ndim == 2:
            batch_size = None
            seq_len = sequence_onehot.shape[0]
        else:
            batch_size = sequence_onehot.shape[0]
            seq_len = sequence_onehot.shape[1]
        
        token_ids = jnp.argmax(sequence_onehot, axis=-1)  # (batch_size, seq_len) 或 (seq_len,)
        embeddings = self.amino_acid_embedding(token_ids)  # (batch_size, seq_len, embed_dim)
        
        if self.use_positional_encoding:
            if batch_size is not None:
                positions = jnp.arange(seq_len)[None, :]  # (1, seq_len)
                positions = jnp.broadcast_to(positions, (batch_size, seq_len))
            else:
                positions = jnp.arange(seq_len)  # (seq_len,)
            
            position_embeddings = self.position_embedding(positions)
            embeddings = embeddings + position_embeddings
        
        embeddings = self.layer_norm(embeddings)
        #embeddings = self.dropout(embeddings, deterministic=not training)
        #embeddings = self.projection(embeddings)
        return embeddings

class SequenceLogitsHead(Module):
    vocab_size: int
    task_type: str = 'sequence'
    use_temperature_scaling: bool = True
    
    def setup(self):
        self.output_layer = Dense(features=self.vocab_size)

        if self.use_temperature_scaling:
            self.temperature = self.param('temperature', 
                                        initializers.constant(1.0), 
                                        ())
    
    def __call__(self, x, return_logits=True, apply_temperature=False):

        logits = self.output_layer(x)
        
        if return_logits:
            if apply_temperature and self.use_temperature_scaling:
                return logits / jnp.maximum(self.temperature, 0.1)
            return logits
        else:

            return jax.nn.softmax(logits, axis=-1)

class get_model_att_sequence_logits(Module):
    seq_len: int
    vocab_size: int = 20
    task_type: str = 'sequence'
    use_temperature_scaling: bool = True
    use_sequence_embedding: bool = True
    embed_dim: int = 1280  # embedding维度
    
    def setup(self):
        if self.use_sequence_embedding:
            self.sequence_embedding = ProteinSequenceEmbedding(
                vocab_size=self.vocab_size,
                embed_dim=self.embed_dim,
                max_seq_len=self.seq_len,
                use_positional_encoding=True
                #dropout_rate=0.1
            )
        
        self.gatedloops = self._create_gatedloops()
        self.attention = MultiHeadSelfAttention(d_model=256, d_h=128, n_head=1, use_causal_mask=False)
        self.mlp_seq = self._create_mlp_seq()
        self.mlp_global = self._create_mlp_glo()
        
        self.sequence_head = SequenceLogitsHead(
            vocab_size=self.vocab_size,
            task_type=self.task_type,
            use_temperature_scaling=self.use_temperature_scaling
        )
        
        
    def _create_gatedloops(self):
        configurations = [
            # {'d_model': self.seq_len+1, 'd_h': 256},
            # {'d_model': 1152, 'd_h': 256},
            {'d_model': 512, 'd_h': 256},
            # {'d_model': 256 * 3, 'd_h': 512},
            {'d_model': 128, 'd_h': 64}
        ]
        return [GatedLinearRNN(**config, 
                               input_activation=nn.tanh,
                               hidden_activation=nn.tanh,
                               gate_activation=nn.sigmoid,
                               use_true_recurrence=True,
                               use_tied_gates=True) for config in configurations]

    def _create_mlp_seq(self):
        layer_sizes = [64, 32]
        return [Dense(features=size) for size in layer_sizes]
    
    def _create_mlp_glo(self):
        layer_sizes = [32, 16, 8, 1]
        return [Dense(features=size) for size in layer_sizes]

    def __call__(self, sequence_onehot, output_type='both', return_logits=True, apply_temperature=False):
        seq_embeddings = self.sequence_embedding(sequence_onehot)
        
        y1, h1 = self.gatedloops[0](seq_embeddings)
        attention_out = self.attention(h1)
        seq_features, h_last = self.gatedloops[1](attention_out[1])
        
        seq_x = h_last
        for dense_layer in self.mlp_seq:
            seq_x = gelu(dense_layer(seq_x))
        
        global_x = seq_features
        # for dense_layer in self.mlp_global:
        #     global_x = gelu(dense_layer(global_x))
        for i, dense_layer in enumerate(self.mlp_global):
            global_x = dense_layer(global_x)
            if i < len(self.mlp_global) - 1:  # 只有非最后一层才应用激活函数
                global_x = gelu(global_x)

        results = {}
        if output_type in ['sequence', 'both']:
            sequence_output = self.sequence_head(seq_x,
                                            return_logits=return_logits,
                                            apply_temperature=apply_temperature)
            if output_type == 'sequence':
                return sequence_output
            results['sequence'] = sequence_output
        
        if output_type in ['global', 'both']:
            global_output = global_x
            if output_type == 'global':
                return global_output
            results['global'] = global_output
        
        return results

def get_model_att_with_sequence_logits(sequence_length, vocab_size=20, 
                                     task_type='both', use_temperature=True,
                                     use_sequence_embedding=True, embed_dim=1280):
    return get_model_att_sequence_logits(
        seq_len=sequence_length,
        vocab_size=vocab_size,
        task_type=task_type,
        use_temperature_scaling=use_temperature,
        use_sequence_embedding=use_sequence_embedding,
        embed_dim=embed_dim
    )
