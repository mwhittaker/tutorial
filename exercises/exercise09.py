# The goal of this exercise is to combine a handful of lessons in a single
# example and to get some practice parallelizing serial code. In this exercise,
# we create a neural network and a gym environment and use the network to do
# some rollouts (that is, we use the neural net to choose actions to take in
# the environment). However, all of the rollouts are done serially.
#
# EXERCISE: Change this code to do rollouts in parallel by making an actor that
# creates both the "env" object and the "policy" object in its constructor. The
# "rollout" function should then be a method of the actor class.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import psutil
import ray
import tensorflow as tf
import time

from ray_tutorial.reinforce.env import BatchedEnv
from ray_tutorial.reinforce.policy import ProximalPolicyLoss
from ray_tutorial.reinforce.filter import MeanStdFilter
from ray_tutorial.reinforce.rollout import rollouts, add_advantage_values

from ray_tutorial.reinforce.env import (NoPreprocessor, AtariRamPreprocessor,
                                        AtariPixelPreprocessor)
from ray_tutorial.reinforce.models.fc_net import fc_net
from ray_tutorial.reinforce.models.vision_net import vision_net

config = {"kl_coeff": 0.2,
          "num_sgd_iter": 30,
          "sgd_stepsize": 5e-5,
          "sgd_batchsize": 128,
          "entropy_coeff": 0.0,
          "clip_param": 0.3,
          "kl_target": 0.01,
          "timesteps_per_batch": 40000}


if __name__ == "__main__":
  ray.init(num_cpus=4, redirect_output=True)

  # For a more interesting example, try this with the following values. Note
  # that this will require installing gym with the atari environments. You'll
  # probably want to use a smaller batchsize for this.
  #
  #     name = "Pong-v0"
  #     preprocessor = AtariPixelPreprocessor()
  @ray.remote
  class Actor(object):
    def __init__(self):
      self.name = "CartPole-v0"
      self.batchsize = 100
      self.preprocessor = NoPreprocessor()
      self.gamma = 0.995
      self.lam = 1.0
      self.horizon = 2000

      # Create a simulator environment. This is a wrapper containing a batch of gym
      # environments. The simulator can be simulated with "env.step(action)", which
      # is called within the "rollouts" function below.
      self.env = BatchedEnv(self.name, self.batchsize, preprocessor=self.preprocessor)

      # Create a neural net policy. Note that we create the neural net inside its
      # own graph. This can help avoid variable name collisions. It shouldn't
      # matter in this example, but if you create a neural net inside of a remote
      # function, and multiple tasks execute that remote function on the same
      # worker, then this can lead to variable name collisions.
      self.policy = None
      self.observation_filter = None
      self.reward_filter = None
      with tf.Graph().as_default():
        sess = tf.Session()
        if self.preprocessor.shape is None:
          self.preprocessor.shape = self.env.observation_space.shape
        self.policy = ProximalPolicyLoss(self.env.observation_space,
                                         self.env.action_space, self.preprocessor,
                                         config, sess)
        self.observation_filter = MeanStdFilter(self.preprocessor.shape,
                                                clip=None)
        self.reward_filter = MeanStdFilter((), clip=None)
        sess.run(tf.global_variables_initializer())

    # Note that directly making this function a remote function will give a
    # pickling error. That happens because when we define a remote function, we
    # pickle the function definition and ship the definition to the workers.
    # However, this function uses "policy", which is a TensorFlow neural net, and
    # TensorFlow often cannot be pickled. This could be addressed by constructing
    # "policy" within the rollout function, but in this case it's better to
    # create an actor that creates the policy in its constructor (so that we can
    # reuse the policy between multiple calls to "rollout").
    def rollout(self):
      # Collect some rollouts.
      trajectory = rollouts(self.policy, self.env, self.horizon,
                            self.observation_filter, self.reward_filter)
      add_advantage_values(trajectory, self.gamma, self.lam, self.reward_filter)
      return trajectory

  actors = [Actor.remote() for _ in range(4)]

  # Do some rollouts to make sure that all of the neural nets have been
  # constructed. This isn't relevant for the serial code, but when we create
  # the neural nets in the background using actors, we don't want the time to
  # create the actors to interfere with the timing measurement below. Make sure
  # that this code uses all of the actors.
  collected_rollouts = [x for actor in actors
                          for x in [actor.rollout.remote() for _ in range(5)]]
  ray.get(collected_rollouts)

  start_time = time.time()

  # Do some rollouts serially. These should be done in parallel.
  collected_rollouts = [r for actor in actors
                          for r in [actor.rollout.remote() for _ in range(5)]]
  collected_rollouts = ray.get(collected_rollouts)

  end_time = time.time()
  duration = end_time - start_time

  expected_duration = np.ceil(20 / psutil.cpu_count(logical=False)) * 0.5
  assert duration < expected_duration, ("Rollouts took {} seconds. This is "
                                        "too slow.".format(duration))

  print("Success! The example took {} seconds.".format(duration))
