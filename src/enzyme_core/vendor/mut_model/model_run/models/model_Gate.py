import jax
from flax import nnx
from flax import linen as nn
from flax.nnx import bridge
from jax import numpy as jnp
from jax.experimental import mesh_utils
from typing import *
from models.model_gated_linear_rnn import GatedLinearRNN
import optax
import matplotlib.pyplot as plt
import numpy as np
from flax.linen import Module,  compact
from flax.linen import initializers
from collections.abc import Callable
from jax import lax
from flax.training import train_state
from jax.nn import gelu

batch_size, sequence_length  = 68, 163
input_dim1 =164
input_dim2 =1152
input_dim3 =1280
d_h, d_model = 32, 18

key1 = jax.random.PRNGKey(42)
key2 = jax.random.PRNGKey(43)
key3 = jax.random.PRNGKey(44)
X1 = jax.random.normal(key1, (batch_size, sequence_length, input_dim1))
X2 = jax.random.normal(key2, (batch_size, sequence_length, input_dim2))
X3 = jax.random.normal(key3, (batch_size, sequence_length, input_dim3))
Y = jax.random.normal(key1, (batch_size))

def dataset(batch_size):
  #while True:
  idx = np.random.choice(len(X1), size=batch_size)
  yield X1[idx],X2[idx],X3[idx], Y[idx]

class TrainState(train_state.TrainState):
  counts: nnx.State
  graphdef: nnx.GraphDef

class Count(nnx.Variable[nnx.A]):
  pass

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
  
class get_model(Module):
  def setup(self):
    #x1
    self.gatedloop_1_1 = GatedLinearRNN(d_model=164,
                                    d_h=256,
                                    input_activation=nn.tanh,
                                    hidden_activation=nn.tanh,
                                    gate_activation=nn.sigmoid,
                                    use_true_recurrence=True,
                                    use_tied_gates=True)
    self.dense_1_2 = Dense(features=128)
    self.dense_1_3 = Dense(features=64)
    #x2
    self.gatedloop_2_1 = GatedLinearRNN(d_model=1152,
                                    d_h=512,
                                    input_activation=nn.tanh,
                                    hidden_activation=nn.tanh,
                                    gate_activation=nn.sigmoid,
                                    use_true_recurrence=True,
                                    use_tied_gates=True)
    self.dense_2_2 = Dense(features=256)
    self.dense_2_3 = Dense(features=128)
    #x3
    self.gatedloop_3_1 = GatedLinearRNN(d_model=1280,
                                    d_h=512,
                                    input_activation=nn.tanh,
                                    hidden_activation=nn.tanh,
                                    gate_activation=nn.sigmoid,
                                    use_true_recurrence=True,
                                    use_tied_gates=True)
    self.dense_3_2 = Dense(features=256)
    self.dense_3_3 = Dense(features=128)
    #concate
    self.dense_0 = Dense(features=128*4)
    self.dense_1 = Dense(features=128*2)
    self.dense_2 = Dense(features=128)
    self.dense_3 = Dense(features=64)
    self.dense_4 = Dense(features=32)
    self.dense_5 = Dense(features=1)
  def __call__(self, x1,x2,x3):
    x1,h1 = self.gatedloop_1_1(x1)
    x1 = gelu(x1)
    x1 = self.dense_1_2(x1)
    x1 = gelu(x1)
    x1 = self.dense_1_3(x1)
    x2,h2 = self.gatedloop_2_1(x2)
    x2 = gelu(x2)
    x2 = self.dense_2_2(x2)
    x2 = gelu(x2)
    x2 = self.dense_2_3(x2)
    x3,h3 = self.gatedloop_3_1(x3)
    x3 = gelu(x3)
    x3 = self.dense_3_2(x3)
    x3 = gelu(x3)
    x3 = self.dense_3_3(x3)
    x = jnp.concatenate([x1,x2,x3],axis=-1)
    x = self.dense_0(x)
    x = gelu(x)
    x = self.dense_1(x)
    x = gelu(x)
    x = self.dense_2(x)
    x = self.dense_3(x)
    x= self.dense_4(x)
    x = self.dense_5(x)
    return x
