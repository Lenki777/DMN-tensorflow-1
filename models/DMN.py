from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random

import numpy as np
from six.moves import xrange 
import tensorflow as tf
from tensorflow.models.rnn import rnn, rnn_cell
from tensorflow.python.ops import variable_scope
import models.seq2seq as seq2seq
import models.cell as cell

class DMN(object):
	"""
		Dynamic Memory Network: it contains four modules: Input, Question, Anwser, Episodic Memory
		check ref: Ask Me Anything: Dynamic Memory Networks for Natural Language Processing
		Args:
			vocab_size: vocabular size
			batch_size: batch size
			learning_rate: learning rate
			embedding_size: embedding size, also the RNN first layer size
			q_depth: question module layer depth
			a_depth: answer module layer depth
			m_depth: memory cell depth
			i_depth: input layer depth (not include input fusion layer)
			memory_hops: how many hops for episodic memory module


		Returns:
			built model of dynamic memory network

	"""
	def __init__(self, vocab_size, embedding_size, learning_rate, 
		learning_rate_decay_op, memory_hops, dropout_rate, 
		q_depth, a_depth, episodic_m_depth, ep_depth,
		attention_ff_l1_size, max_gradient_norm, maximum_story_length=20,
		maximum_question_length=20, use_lstm=False, forward_only=False):
	
		# initialization
		self.vocab_size = vocab_size
		self.embedding_size = embedding_size
		self.learning_rate = tf.Variable(float(learning_rate), trainable=False)
		self.learning_rate_decay_op = tf.Variable(float(learning_rate_decay_op), trainable=False)
		self.dropout_rate = dropout_rate
		self.global_step = tf.Variable(0, trainable=False, name='global_step')
		self.q_depth = q_depth	# question RNN depth
		self.a_depth = a_depth	# answer RNN depth
		self.m_depth = episodic_m_depth # memory cell depth
		self.ep_depth = ep_depth	# episodic depth
		self.max_gradient_norm = max_gradient_norm
		self.memory_hops = memory_hops	# number of episodic memory pass
		self.m_input_size = embedding_size * 4
		self.m_size = embedding_size # memory cell size
		self.a_size = embedding_size
		self.attention_ff_l1_size = attention_ff_l1_size 
		self.maximum_story_length = maximum_story_length
				
		
		print("[*] Creating Dynamic Memory Network ...")
		# Initializing word2vec
		W = tf.Variable(tf.constant(0.0, shape=[vocab_size, embedding_size]),
                trainable=False, name="W")
		self.embedding_placeholder = tf.placeholder(tf.float32, [vocab_size, embedding_size])
		self.embedding_init = W.assign(self.embedding_placeholder)

		# attention gate in episodic
		# TODO: force gate logits to be sparse, add L1 norm regularization


		# Sentence token placeholder
		self.story = []
		story_embedded = []
		for i in range(maximum_story_length):
			self.story.append(tf.placeholder(tf.int32, shape=[None,None], name="Story"))
			story_embedded.append(tf.nn.embedding_lookup(W, self.story[i]))
			story_embedded[i] = tf.transpose(story_embedded[i],[1,0,2])

		self.story_len = tf.placeholder(tf.int64, shape=[1], name="Story_length")

		self.question = tf.placeholder(tf.int32, shape=[None,None], name="Question")
		question_embedded = tf.transpose(tf.nn.embedding_lookup(W, self.question), [1,0,2])
		self.answer = tf.placeholder(tf.int64, name="answer")


		# configuration of attention gate
		
		softmax_weights = tf.Variable(tf.truncated_normal([self.a_size, self.vocab_size], -0.1, 0.1), name="softmax_weights")
		softmax_biases = tf.Variable(tf.zeros([self.vocab_size]), name="softmax_biases")
		
		answer_weights = tf.Variable(tf.truncated_normal([self.m_size, self.a_size], -0.1, 0.1), name="answer_weights")	

		#------------ question module ------------
		embedding_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		with tf.variable_scope("embedding_rnn"):
			_, self.question_state = rnn.dynamic_rnn(embedding_cell, question_embedded, dtype=tf.float32, time_major=True)

		#------------ Input module ------------
		self.story_state_array = []
		with tf.variable_scope("embedding_rnn", reuse=True):
			for i in range(maximum_story_length):
				_, story_states = rnn.dynamic_rnn(embedding_cell, story_embedded[i], dtype=tf.float32, time_major=True)
				self.story_state_array.append(story_states)

		fusion_fw_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		fusion_bw_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		(self.facts, _, _) = rnn.bidirectional_rnn(fusion_fw_cell,fusion_bw_cell, self.story_state_array, dtype=tf.float32)	
		
		#------------ episodic memory module ------------	
		self.ep_size = 2*self.embedding_size# episodic cell size
		# construct memory cell	
		mem_cell = cell.MemCell(self.m_size)
		#mem_cell = tf.nn.rnn_cell.GRUCell(self.m_size)
		self.episodic_array = tf.Variable(tf.random_normal([1,1]))

		# construct episodic_cell	
		single_cell = cell.MGRUCell(self.ep_size)
		ep_cell = cell.MultiMGRUCell([single_cell] * ep_depth)

		q_double = tf.concat(1, [self.question_state, self.question_state])
		mem_state_double = q_double

		# TODO change z_dim to be 
		z_dim = self.embedding_size * 8
		attention_ff_size = z_dim
		attention_ff_l2_size = 1 
	
		with tf.variable_scope("feedforward_nn"):
			l1_weights = tf.get_variable("l1_weights", [attention_ff_size, attention_ff_l1_size], 
				initializer=tf.random_normal_initializer())
			l1_biases = tf.get_variable("l1_biases", [attention_ff_l1_size], 
				initializer=tf.random_normal_initializer())
			l2_weights = tf.get_variable("l2_weights", [attention_ff_l1_size, attention_ff_l2_size], 
				initializer=tf.random_normal_initializer())
			l2_biases = tf.get_variable("l2_biases", [attention_ff_l2_size], 
				initializer=tf.random_normal_initializer())
		def feedforward_nn(step, l1_input):
			with tf.variable_scope("feedforward_nn", reuse=True):
				l2_input = tf.tanh(tf.matmul(l1_input , l1_weights) + l1_biases)
				gate_prediction = tf.matmul(l2_input , l2_weights) + l2_biases	
				return gate_prediction

		# -------- multi-layer feedforward for multi-hop propagation -----------
		mem_weights = dict()	
		for hops in xrange(self.memory_hops):
			mem_weights[hops] = dict()
			mem_weights[hops]["weights"] = tf.Variable(tf.truncated_normal([self.m_input_size, self.m_size], -0.1, 0.1))
			mem_weights[hops]["biases"] = tf.Variable(tf.zeros([self.m_size]))


		episodic_array = []
		hops = tf.Variable(0, trainable=False)
		for step in range(maximum_story_length):
			z = tf.concat(1, [tf.mul(self.facts[step], q_double), tf.mul(self.facts[step], mem_state_double), 
			tf.abs(tf.sub(self.facts[step], q_double)), tf.abs(tf.sub(self.facts[step], mem_state_double))])
			episodic_array.append(feedforward_nn(step, z))

		self.episodic_array_reshaped = tf.reshape(tf.concat(0,episodic_array), [1,-1])
		self.episodic_gate = tf.nn.softmax(self.episodic_array_reshaped)
		self.episodic_gate_unpacked = tf.unpack( tf.reshape(self.episodic_gate, [20,1]))
		# attention GRU	
		with tf.variable_scope("episodic", reuse=None):
			output, context = cell.rnn_ep(ep_cell, self.facts, self.episodic_gate_unpacked, dtype=tf.float32)
			# memory updates
			mem_state_current = mem_cell(context, self.question_state, self.question_state, mem_weights, self.m_input_size, self.m_size, 1)
			mem_state_previous = mem_state_current
		self.argmax_ep_gate = tf.argmax(self.episodic_gate, 1)
		

		def body(argmax_ep_gate, hops, mem_state_previous, mem_state_current):
			episodic_array = []
			mem_state_double = tf.concat(1, [mem_state_previous, mem_state_previous])
			for step in range(maximum_story_length):
				z = tf.concat(1, [tf.mul(self.facts[step], q_double), tf.mul(self.facts[step], mem_state_double), 
				tf.abs(tf.sub(self.facts[step], q_double)), tf.abs(tf.sub(self.facts[step], mem_state_double))])
				episodic_array.append(feedforward_nn(step, z))

			self.episodic_array_reshaped = tf.reshape(tf.concat(0,episodic_array), [1,-1])
			self.episodic_gate = tf.nn.softmax(self.episodic_array_reshaped)
			self.episodic_gate_unpacked = tf.unpack( tf.reshape(self.episodic_gate, [20,1]))
			# attention GRU
			# with tf.variable_scope("episodic", reuse=True if hops > 0 else None):
			with tf.variable_scope("episodic", reuse=True):
				output, context = cell.rnn_ep(ep_cell, self.facts, self.episodic_gate_unpacked, dtype=tf.float32)
				# memory updates	
				mem_state_previous = mem_state_current
				mem_state_current = mem_cell(context, self.question_state, mem_state_previous, mem_weights, self.m_input_size, self.m_size, self.memory_hops)

			self.argmax_ep_gate = tf.argmax(self.episodic_gate, 1)
			hops = tf.add(hops,1)
			return self.argmax_ep_gate, hops, mem_state_previous, mem_state_current
		def condition(argmax_ep_gate, hops, mem_state_previous, mem_state_current):	
			return tf.logical_and(tf.less(argmax_ep_gate,self.story_len)[0],tf.less(hops,tf.constant(20)))

		_, _, mem_state, _ = tf.while_loop(condition, body, [self.argmax_ep_gate, hops, mem_state_previous, mem_state_current])
	
		#------------ answer ------------
		# TODO: use decoder sequence to generate answer
		answer_steps = 1
		single_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		answer_cell = single_cell
		if a_depth > 1:
			answer_cell =tf.nn.rnn_cell.MultiRNNCell([single_cell] * a_depth)

		a_state = mem_state
		for step in range(answer_steps):
			y = tf.nn.softmax(tf.matmul(a_state, answer_weights))
			(answer, a_state) = answer_cell(tf.concat(1, [self.question_state, y]), a_state)	

		self.logits = tf.nn.softmax(tf.matmul(answer, softmax_weights)+softmax_biases)

		answer = tf.reshape(tf.one_hot(self.answer, self.vocab_size, 1.0, 0.0), [1,self.vocab_size])
		self.loss = tf.reduce_mean(
			tf.nn.softmax_cross_entropy_with_logits(self.logits, answer))
	
		params = tf.trainable_variables()
		#for e in params:
		#	print(e.get_shape(), e.name, e)
		if not forward_only:
			self.gradient_norms = []
			self.updates = []
			optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
			gradients = tf.gradients(self.loss, params)
			clipped_gradients, norm = tf.clip_by_global_norm(gradients,
				self.max_gradient_norm)
			self.gradient_norms = norm
			self.updates = optimizer.apply_gradients(
				zip(clipped_gradients, params))#, global_step=self.global_step)
		
		self.saver = tf.train.Saver(tf.all_variables())
	
	def step(self, session, story, story_mask, question, answer, forward_only):
		input_feed = {}
		# split story according to story_mask and pad it to maximum story length
		story_splited = [[] for i in story_mask]
		story_count = 0
		for i in range(len(story)):
			story_splited[story_count].append(story[i])
			if i == story_mask[story_count]:
				story_count += 1
		for i in range(len(story_mask),self.maximum_story_length):
			story_splited.append([0])
		for l in range(self.maximum_story_length):
			input_feed[self.story[l].name] = [story_splited[l]]

		input_feed[self.question.name] = [question]
		input_feed[self.answer.name] = answer	
		input_feed[self.story_len.name] = [len(story_mask)]

		if not forward_only:
			output_feed = [
							self.updates,	# Update Op that does SGD.
							self.gradient_norms,	# Gradient norm.
							self.loss]	# Loss for this batch.
							# # debugging
							# self.story_vec[0],	
							# self.logits,
							# self.question_state,
							# self.facts,
							# self.episodic_gate]
		else:
			output_feed = [self.loss,		# Loss for this batch.
							tf.argmax(self.logits, 0),
							# debugging
							self.logits,
							self.question_state,
							self.facts,
							self.episodic_gate]

		outputs = session.run(output_feed, input_feed)
		if not forward_only:	
			return outputs[1], outputs[2], None  # Gradient norm, loss, no outputs.
		else:
			return None, outputs[0], outputs[1]  # No gradient norm, loss, outputs.

	def init_embedding(self, sess, embedding):
		sess.run(self.embedding_init, feed_dict={self.embedding_placeholder.name: embedding})

