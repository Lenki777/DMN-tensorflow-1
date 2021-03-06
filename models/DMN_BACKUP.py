from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random

import numpy as np
from six.moves import xrange 
import tensorflow as tf
from tensorflow.models.rnn import rnn, rnn_cell

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
		m_input_size, attention_ff_l1_size, max_gradient_norm, maximum_story_length=100,
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
		self.m_input_size = m_input_size
		self.m_size = embedding_size # memory cell size
		self.a_size = embedding_size # answer RNN size


		self.attention_ff_l1_size = attention_ff_l1_size
		# attention_ff_l2_size 

		
		
		print("[*] Creating Dynamic Memory Network ...")
		# question module
		def seq2seq_fq(encoder_inputs, cell, mask=None):
			return seq2seq.sentence_embedding_rnn_q(
				encoder_inputs, self.vocab_size, cell, self.embedding_size, mask)
		def seq2seq_fs(encoder_inputs, cell, mask=None):
			return seq2seq.sentence_embedding_rnn_s(
				encoder_inputs, self.vocab_size, cell, self.embedding_size, mask)
		# attention gate in episodic
		# TODO: force gate logits to be sparse, add L1 norm regularization





		# Sentence token placeholder
		self.story = []
		for i in range(maximum_story_length):
			self.story.append(tf.placeholder(tf.int32, shape=[None], 
												name="story{0}".format(i)))
		self.story_mask = tf.placeholder(tf.int32, shape=[None], name="story_mask")
		self.story_len = tf.placeholder(tf.int32, shape=[], name="story length")
		print (self.story_len)
		self.question = []
		for i in range(maximum_question_length):
			self.question.append(tf.placeholder(tf.int32, shape=[None], name="question{0}".format(i)))
		self.answer = tf.placeholder(tf.int64, name="answer")

		# self.story_len = 1#= tf.reshape(tf.shape(self.story_mask), [])
		# TODO: fixed lens problem
		#self.story_len = 5

		# configuration of attention gate

		# print (self.story)
		


		with tf.variable_scope("answer"):
			softmax_weights = tf.Variable(tf.truncated_normal([self.a_size, self.vocab_size], -0.1, 0.1), name="softmax_weights")
			softmax_biases = tf.Variable(tf.zeros([self.vocab_size]), name="softmax_biases")
		
		answer_weights = tf.Variable(tf.truncated_normal([self.m_size, self.a_size], -0.1, 0.1), name="answer_weights")
		answer_biases = tf.Variable(tf.zeros([self.a_size]), name="answer_biases")

		#------------ question module ------------
		single_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		if use_lstm:
			single_cell = tf.nn.rnn_cell.BasicLSTMCell(self.embedding_size)
		if not forward_only and dropout_rate < 1:
			single_cell = tf.nn.rnn_cell.DropoutWrapper(
				single_cell, output_keep_prob=dropout_rate)
		question_cell = single_cell
		if q_depth > 1:
			question_cell = tf.nn.rnn_cell.MultiRNNCell([single_cell]*q_depth)
		question = seq2seq_fq(self.question, question_cell)
		self.question_state = question[0]
		#for e in question:


		#------------ Input module ------------
		reader_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		if use_lstm:
			reader_cell = tf.nn.rnn_cell.BasicLSTMCell(self.embedding_size)
		if not forward_only and dropout_rate < 1:
			reader_cell = tf.nn.rnn_cell.DropoutWrapper(
				reader_cell, output_keep_prob=dropout_rate)
		# Embedded toekn into vector, feed into rnn cell return cell state
		fusion_fw_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		fusion_bw_cell = tf.nn.rnn_cell.GRUCell(self.embedding_size)
		if use_lstm:
			fusion_fw_cell = tf.nn.rnn_cell.BasicLSTMCell(self.embedding_size)
			fusion_bw_cell = tf.nn.rnn_cell.BasicLSTMCell(self.embedding_size)

		if not forward_only and dropout_rate < 1:
			fusion_fw_cell = tf.nn.rnn_cell.DropoutWrapper(
				fusion_fw_cell, output_keep_prob=dropout_rate)
			fusion_bw_cell = tf.nn.rnn_cell.DropoutWrapper(
				fusion_bw_cell, output_keep_prob=dropout_rate)

		(_facts, _, _) = rnn.bidirectional_rnn(fusion_fw_cell,fusion_bw_cell,
			seq2seq_fs(self.story, reader_cell),dtype=tf.float32)

		self.facts = _facts[0]




		#------------ episodic memory module ------------
		# TODO: use self.facts to extract ep_size
		self.ep_size = 2*self.embedding_size# episodic cell size
		# construct memory cell
		#single_cell = tf.nn.rnn_cell.BasicLSTMCell(self.m_size)
		mem_cell = cell.MemCell(self.m_size)
		#mem_cell = tf.nn.rnn_cell.GRUCell(self.m_size)
		self.episodic_array = tf.Variable(tf.random_normal([1,1]))

		# construct episodic_cell

		# for i in xrange(self.memory_hops):
		single_cell = cell.MGRUCell(self.ep_size)
		ep_cell = cell.MultiMGRUCell([single_cell] * ep_depth)

		e = []
		mem_state = self.question_state
		q_double = tf.concat(1, [self.question_state, self.question_state])
		mem_state_double = tf.concat(1, [mem_state, mem_state])

		# TODO change z_dim to be 
		z_dim = self.embedding_size * 8
		self.attention_ff_size = z_dim
		self.attention_ff_l2_size = 1 
		# self._ep_initial_state = []
		# for _cell in range(ep_cell)
		# 	self._ep_initial_state.append = _cell.zero_state(1, tf.float32)	# TODO change batch size

		# initialize parameters	
		with tf.variable_scope("episodic"):
			# parameters of attention gate
			l1_weights = tf.Variable(tf.truncated_normal([self.attention_ff_size, self.attention_ff_l1_size], -0.1, 0.1), name="l1_weights")
			l1_biases = tf.Variable(tf.zeros([self.attention_ff_l1_size]), name="l1_biases")
			l2_weights = tf.Variable(tf.truncated_normal([self.attention_ff_l1_size, self.attention_ff_l2_size], -0.1, 0.1), name="l2_weights")
			l2_biases = tf.Variable(tf.zeros([self.attention_ff_l2_size]), name="l2_biases")
			# paramters of episodic
			mem_weights = tf.Variable(tf.truncated_normal([self.m_input_size, self.m_size], -0.1, 0.1), name="mem_weights")
			mem_biases = tf.Variable(tf.zeros([self.m_size]), name="mem_biases")


		# initializing variable of feedforward nn
		seq2seq.def_feedforward_nn(self.attention_ff_size, self.attention_ff_l1_size, self.attention_ff_l2_size)

		for hops in xrange(self.memory_hops):
			# gate attention network
			step = tf.constant(0)
			tf.while_loop(lambda step, story_len, facts, q_double, mem_state_double: tf.less(step, story_len),
				lambda step, story_len, facts, q_double, mem_state_double: self.mem_body(step, story_len, facts, q_double, mem_state_double),
				[step, self.story_len, self.facts, q_double, mem_state_double])	

			#self.episodic_gate = tf.reshape(tf.nn.softmax(self.episodic_array),[1])
			self.episodic_gate = tf.nn.softmax(tf.reshape(self.episodic_array, [1,-1]))
			print ("episodic_gate",self.episodic_gate)

			# attention GRU
			# output, context = cell.rnn(ep_cell[hops], [self.facts], self.episodic_gate, scope="epsodic", dtype=tf.float32)
			output, context = cell.rnn_ep(ep_cell, [self.facts], self.episodic_gate, dtype=tf.float32, scope="episodic")
			e.append(output)
			# memory updates
			#_, mem_state = mem_cell(context_state, mem_state)	# GRU
			#_, mem_state = cell.rnn_mem(mem_cell, [context], self.question_state, mem_state, self.m_input_size, self.m_size, dtype=tf.float32)
			mem_state = mem_cell(context,  self.question_state, mem_state, self.m_input_size, self.m_size)

			# if the attentioned module is last e, it means the episodic pass is over
			if np.argmax(np.asarray(e[-1])) == len(e[-1])-1:
				break
			
			
		#------------ answer ------------
		# TODO: use decoder sequence to generate answer
		answer_steps = 1
		single_cell = tf.nn.rnn_cell.GRUCell(self.a_size)
		answer_cell = single_cell
		if a_depth > 1:
			answer_cell =tf.nn.rnn_cell.MultiRNNCell([single_cell] * a_depth)
		
		a_state = mem_state
		for step in range(answer_steps):
			y = tf.nn.softmax(tf.matmul(a_state, answer_weights))
			(answer, a_state) = answer_cell(tf.concat(1, [self.question_state, y]), a_state)
			#(answer, a_state) = answer_cell(tf.concat(1, [question, mem_state]), a_state)

		self.logits = tf.nn.softmax(tf.matmul(answer, softmax_weights)+softmax_biases)
		answer = tf.reshape(tf.one_hot(self.answer, self.vocab_size, 1.0, 0.0), [1,self.vocab_size])
		self.loss = tf.reduce_mean(
			tf.nn.softmax_cross_entropy_with_logits(self.logits, answer))

		
		params = tf.trainable_variables()
		# testing
		for e in params:
			print(e.get_shape(), e.name, type(e))
		if not forward_only:
			self.gradient_norms = []
			self.updates = []
			optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
			gradients = tf.gradients(self.loss, params)
			clipped_gradients, norm = tf.clip_by_global_norm(gradients,
				self.max_gradient_norm)
			self.gradient_norms = norm
			self.updates = optimizer.apply_gradients(
				zip(clipped_gradients, params), global_step=self.global_step)
		
		self.saver = tf.train.Saver(tf.all_variables())

	def step(self, session, story, story_mask, question, answer, forward_only):
		input_feed = {}
		for l in range(len(story)):
			input_feed[self.story[l].name] = [story[l]]
		for l in range(len(story),100):
			input_feed[self.story[l].name] = [0]
		# input_feed[self.story_len_.name]= len(story_mask)
		for l in range(len(question)):
			input_feed[self.question[l].name] = [question[l]]
		for l in range(len(question),20):
			input_feed[self.question[l].name] = [0]
		# for l in range(len([answer])):
		input_feed[self.answer.name] = answer
		input_feed[self.story_mask.name] = story_mask
		print ("????????",len(story_mask))
		input_feed[self.story_len.name] = len(story_mask)

		print ("---------------------", session.run(self.story_len))
		if not forward_only:
			output_feed = [self.updates,	# Update Op that does SGD.
							self.gradient_norms,	# Gradient norm.
							self.loss]	# Loss for this batch.
		else:
			output_feed = [self.loss,		# Loss for this batch.
							tf.argmax(self.logits, 0)]

		outputs = session.run(output_feed, input_feed)
		if not forward_only:
			return outputs[1], outputs[2], None  # Gradient norm, loss, no outputs.
		else:
			return None, outputs[0], outputs[1]  # No gradient norm, loss, outputs.

	# def get_qns(self, data_set):
	# 	"""Provide data set; return question and story"""
	# def mem_body(self, step, story_len, facts, q_double, mem_state_double):
	# 	print("=++++++++++++++++")
	# 	print("step:",step)
	# 	z = tf.concat(1, [tf.mul(facts[step, :], q_double), tf.mul(facts[step, :], mem_state_double), 
	# 		tf.abs(tf.sub(facts[step, :], q_double)), tf.abs(tf.sub(facts[step, :], mem_state_double))])
	# 	# record Z (all episodic memory states)
	# 	print("-----------------")
	# 	self.episodic_array.append(feedforward_nn(z, attention_ff_size, attention_ff_l1_size, attention_ff_l2_size))
		
	# 	step =tf.add(step, 1)
	# 	return step
	def mem_body(self, step, story_len, facts, q_double, mem_state_double):
		print ("!!!!!!!!!!!!!!!!!!!!!")
		z = tf.concat(1, [tf.mul(tf.gather(facts, step), q_double), tf.mul(tf.gather(facts, step), mem_state_double), 
			tf.abs(tf.sub(tf.gather(facts, step), q_double)), tf.abs(tf.sub(tf.gather(facts, step), mem_state_double))])
		# record Z (all episodic memory states)
		def f1(): return seq2seq.feedforward_nn(z, self.attention_ff_size, self.attention_ff_l1_size, self.attention_ff_l2_size)
		def f2(): return tf.concat(0, [tf.reshape(tf.to_float(self.episodic_array),[-1]), tf.reshape(seq2seq.feedforward_nn(z, self.attention_ff_size, self.attention_ff_l1_size, self.attention_ff_l2_size),[-1])])
		
		self.episodic_array = tf.cond(tf.less(step,1), f1, f2)
		print (self.episodic_array)
		print ('=-=-=-=-=', tf.to_float(self.episodic_array), seq2seq.feedforward_nn(z, self.attention_ff_size, self.attention_ff_l1_size, self.attention_ff_l2_size))
		step =tf.add(step, 1)
		return step, story_len, facts, q_double, mem_state_double
