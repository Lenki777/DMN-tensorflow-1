from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random

import numpy as np
from six.moves import xrange 
import tensorflow as tf

import Question
import Input
import Answer
import Episodic

class DMN(object):
	def __init__(self, forward_only=False):
		self.vocab_size = vocab_size
		self.batch_size = batch_size
		self.learning_rate = tf.Variable(float(learning_rate), trainable=False)
		self.global_step = tf.Variable(0, trainable=False, name='global_step')

		self._input_data = tf.placeholder(tf.int32, [])

		# Question module


		# Episodic module


		# Input module


		# Answer module




	def step(self, forward_only):
		