
from flax import linen as nn
from jax.nn import gelu
from typing import Any
import jax.numpy as jnp

class CausalTimeMixing(nn.Module):
    eps: float
    dropout: float
    model: Any

    def setup(self):
        self.layer_norm_function = nn.LayerNorm(epsilon=self.eps)
        self.dropout_function = nn.Dropout(rate=self.dropout)

    def __call__(self, x, training: bool, carry=None, mask=None):
        """
        :param      x           float     (batch_size, seq_len, d_model)         required
        :param      training    bool                                            optional
        :return:    y           float     (batch_size, seq_len, d_model)
        """
        residual = x
        x = self.layer_norm_function(x)
        h, x = self.model(x, carry=carry, mask=mask)
        x = x + residual
        x = self.dropout_function(x, deterministic=not training)
        return h, x


class ChannelMixing(nn.Module):
    d_models: int
    dropout: float
    eps: float
    def setup(self):
        self.n_layers = len(self.d_models) - 1
        self.dense_layers = [nn.Dense(self.d_models[i+1]) for i in range(self.n_layers)]
        self.layer_norm = nn.LayerNorm(epsilon=self.eps)
        self.dropout_function = nn.Dropout(rate=self.dropout)

    def __call__(self, x, training: bool):
        residual = x
        x = self.layer_norm(x)
        for i in range(self.n_layers):
            x = self.dense_layers[i](x)
            if i != self.n_layers - 1:  # No activation after the last layer
                x = gelu(x)
        x = x + residual
        x = self.dropout_function(x, deterministic=not training)
        return x



class SequenceModel(nn.Module):
    n_layer: int
    d_model: int
    d_channel_mixing: int
    eps: float
    channel_mixing_dropout: float
    time_mixing_dropout: float
    input_vocab_size: int
    output_vocab_size: int
    max_seq_length: int
    embedding_dropout: float
    use_word_embedding: bool
    positional_encoding_mode: str
    use_head: bool

    def setup(self):
        self.channel_mixing_layers = [ChannelMixing(
            d_models=[self.d_model, self.d_channel_mixing, self.d_model],
            dropout=self.channel_mixing_dropout,
            eps=self.eps
        ) for _ in range(self.n_layer)]
        if self.positional_encoding_mode == 'learned':
            self.wpe = nn.Embed(self.max_seq_length, self.d_model)
        elif self.positional_encoding_mode == 'sinusoidal':
            self.wpe = SinusoidalPositionalEncoding(max_seq_length=self.max_seq_length, emb_dim=self.d_model)
        elif self.positional_encoding_mode == "none":
            pass
        else:
            raise NotImplementedError
        if self.input_vocab_size is None and self.use_word_embedding is True:
            raise AttributeError("self.input_vocab_size is None and self.use_word_embedding")
        if self.use_head is True:
            self.head = nn.Dense(self.output_vocab_size)
        self.embedding_dropout_function = nn.Dropout(rate=self.embedding_dropout)
        if self.use_word_embedding:
            self.input_function = nn.Embed(self.input_vocab_size, self.d_model)
        else:
            self.input_function = nn.Dense(self.d_model)

    def __call__(self, x, training: bool, carry=None, mask=None):
        seq_length = x.shape[1]
        x = self.input_function(x)
        if self.positional_encoding_mode == 'sinusoidal' or self.positional_encoding_mode == 'learned':
            x = x + self.wpe(seq_length)
        x = self.embedding_dropout_function(x, deterministic=not training)
        h = []
        for l, (time_mixing, channel_mixing) in enumerate(zip(self.time_mixing_layers, self.channel_mixing_layers)):
            h_l, x = time_mixing(x, training, carry=(carry[:, l, :] if carry is not None else None), mask=mask)
            x = channel_mixing(x, training)
            if h_l is not None:
                h.append(h_l)
        if h:
            h = jnp.stack(h, axis=1)
        if self.use_head is True:
            x = self.head(x)
        return h, x

class SinusoidalPositionalEncoding(nn.Module):
    max_seq_length: int
    emb_dim: int

    def setup(self):
        position = jnp.arange(self.max_seq_length)[:, None]
        div_term = jnp.exp(jnp.arange(0, self.emb_dim, 2) *
                           -(jnp.log(10000.0) / self.emb_dim))
        positional_encoding = jnp.zeros((1, self.max_seq_length, self.emb_dim))
        positional_encoding = positional_encoding.at[:, :, 0::2].set(jnp.sin(position * div_term))
        positional_encoding = positional_encoding.at[:, :, 1::2].set(jnp.cos(position * div_term))
        self.positional_encoding = positional_encoding

    def __call__(self, seq_len):
        return self.positional_encoding[:, :seq_len, :]
