import numpy as np
from copy import deepcopy

from mushroom.algorithms.agent import Agent
from mushroom.approximators import Regressor
from mushroom.approximators.parametric import LinearApproximator
from mushroom.features import get_action_features
from mushroom.utils.eligibility_trace import EligibilityTrace
from mushroom.utils.table import EnsembleTable, Table


class TD(Agent):
    """
    Implements functions to run TD algorithms.

    """
    def __init__(self, approximator, policy, mdp_info, learning_rate,
                 features=None):
        """
        Constructor.

        Args:
            approximator (object): the approximator to use to fit the
               Q-function;
            learning_rate (Parameter): the learning rate.

        """
        self.alpha = learning_rate

        policy.set_q(approximator)
        self.approximator = approximator

        super().__init__(policy, mdp_info, features)

    def fit(self, dataset):
        assert len(dataset) == 1

        state, action, reward, next_state, absorbing = self._parse(dataset)
        self._update(state, action, reward, next_state, absorbing)

    @staticmethod
    def _parse(dataset):
        """
        Utility to parse the dataset that is supposed to contain only a sample.

        Args:
            dataset (list): the current episode step.

        Returns:
            A tuple containing state, action, reward, next state, absorbing and
            last flag.

        """
        sample = dataset[0]
        state = sample[0]
        action = sample[1]
        reward = sample[2]
        next_state = sample[3]
        absorbing = sample[4]

        return state, action, reward, next_state, absorbing

    def _update(self, state, action, reward, next_state, absorbing):
        """
        Update the Q-table.

        Args:
            state (np.ndarray): state;
            action (np.ndarray): action;
            reward (np.ndarray): reward;
            next_state (np.ndarray): next state;
            absorbing (np.ndarray): absorbing flag.

        """
        pass


class QLearning(TD):
    """
    Q-Learning algorithm.
    "Learning from Delayed Rewards". Watkins C.J.C.H.. 1989.

    """
    def __init__(self, policy, mdp_info, learning_rate):
        self.Q = Table(mdp_info.size)

        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        q_current = self.Q[state, action]

        q_next = np.max(self.Q[next_state, :]) if not absorbing else 0.

        self.Q[state, action] = q_current + self.alpha(state, action) * (
            reward + self.mdp_info.gamma * q_next - q_current)


class DoubleQLearning(TD):
    """
    Double Q-Learning algorithm.
    "Double Q-Learning". Hasselt H. V.. 2010.

    """
    def __init__(self, policy, mdp_info, learning_rate):
        self.Q = EnsembleTable(2, mdp_info.size)

        super().__init__(self.Q, policy, mdp_info, learning_rate)

        self.alpha = [deepcopy(self.alpha), deepcopy(self.alpha)]

        assert len(self.Q) == 2, 'The regressor ensemble must' \
                                 ' have exactly 2 models.'

    def _update(self, state, action, reward, next_state, absorbing):
        approximator_idx = 0 if np.random.uniform() < .5 else 1

        q_current = self.Q[approximator_idx][state, action]

        if not absorbing:
            q_ss = self.Q[approximator_idx][next_state, :]
            max_q = np.max(q_ss)
            a_n = np.array(
                [np.random.choice(np.argwhere(q_ss == max_q).ravel())])
            q_next = self.Q[1 - approximator_idx][next_state, a_n]
        else:
            q_next = 0.

        q = q_current + self.alpha[approximator_idx](state, action) * (
            reward + self.mdp_info.gamma * q_next - q_current)

        self.Q[approximator_idx][state, action] = q


class WeightedQLearning(TD):
    """
    Weighted Q-Learning algorithm.
    "Estimating the Maximum Expected Value through Gaussian Approximation".
    D'Eramo C. et. al.. 2016.

    """
    def __init__(self, policy, mdp_info, learning_rate, sampling=True,
                 precision=1000, weighted_policy=False):
        """
        Constructor.

        Args:
            sampling (bool, True): use the approximated version to speed up
                the computation;
            precision (int, 1000): number of samples to use in the approximated
                version;
            weighted_policy (bool, False): whether to use the weighted policy
                or not.


        """
        self.Q = Table(mdp_info.size)
        self._sampling = sampling
        self._precision = precision

        super().__init__(self.Q, policy, mdp_info, learning_rate)

        self._n_updates = Table(mdp_info.size)
        self._sigma = Table(mdp_info.size, initial_value=1e10)
        self._Q = Table(mdp_info.size)
        self._Q2 = Table(mdp_info.size)
        self._weights_var = Table(mdp_info.size)

        self._use_weighted_policy = weighted_policy

    def _update(self, state, action, reward, next_state, absorbing):
        q_current = self.Q[state, action]
        q_next = self._next_q(next_state) if not absorbing else 0.

        target = reward + self.mdp_info.gamma * q_next

        alpha = self.alpha(state, action)

        self.Q[state, action] = q_current + alpha * (target - q_current)

        self._n_updates[state, action] += 1

        self._Q[state, action] += (
            target - self._Q[state, action]) / self._n_updates[state, action]
        self._Q2[state, action] += (target ** 2. - self._Q2[
            state, action]) / self._n_updates[state, action]
        self._weights_var[state, action] = (
            1 - alpha) ** 2. * self._weights_var[state, action] + alpha ** 2.

        if self._n_updates[state, action] > 1:
            var = self._n_updates[state, action] * (
                self._Q2[state, action] - self._Q[state, action] ** 2.) / (
                self._n_updates[state, action] - 1.)
            var_estimator = var * self._weights_var[state, action]
            var_estimator = np.maximum(var_estimator, 1e-10)
            self._sigma[state, action] = np.sqrt(var_estimator)

    def _next_q(self, next_state):
        """
        Args:
            next_state (np.ndarray): the state where next action has to be
                evaluated.

        Returns:
            The weighted estimator value in ``next_state``.

        """
        means = self.Q[next_state, :]
        sigmas = np.zeros(self.Q.shape[-1])

        for a in range(sigmas.size):
            sigmas[a] = self._sigma[next_state, np.array([a])]

        if self._sampling:
            samples = np.random.normal(np.repeat([means], self._precision, 0),
                                       np.repeat([sigmas], self._precision, 0))
            max_idx = np.argmax(samples, axis=1)
            max_idx, max_count = np.unique(max_idx, return_counts=True)
            count = np.zeros(means.size)
            count[max_idx] = max_count

            self._w = count / self._precision
        else:
            raise NotImplementedError

        if self._use_weighted_policy:
            self.next_action = np.array(
                [np.random.choice(self.mdp_info.action_space.n, p=self._w)])

        return np.dot(self._w, means)


class SpeedyQLearning(TD):
    """
    Speedy Q-Learning algorithm.
    "Speedy Q-Learning". Ghavamzadeh et. al.. 2011.

    """
    def __init__(self, policy, mdp_info, learning_rate):
        self.Q = Table(mdp_info.size)
        self.old_q = deepcopy(self.Q)

        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        old_q = deepcopy(self.Q)

        max_q_cur = np.max(self.Q[next_state, :]) if not absorbing else 0.
        max_q_old = np.max(self.old_q[next_state, :]) if not absorbing else 0.

        target_cur = reward + self.mdp_info.gamma * max_q_cur
        target_old = reward + self.mdp_info.gamma * max_q_old

        alpha = self.alpha(state, action)
        q_cur = self.Q[state, action]
        self.Q[state, action] = q_cur + alpha * (target_old - q_cur) + (
            1. - alpha) * (target_cur - target_old)

        self.old_q = old_q


class SARSA(TD):
    """
    SARSA algorithm.

    """
    def __init__(self, policy, mdp_info, learning_rate):
        self.Q = Table(mdp_info.size)
        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        q_current = self.Q[state, action]

        self.next_action = self.draw_action(next_state)
        q_next = self.Q[next_state, self.next_action] if not absorbing else 0.

        self.Q[state, action] = q_current + self.alpha(state, action) * (
            reward + self.mdp_info.gamma * q_next - q_current)


class SARSALambdaDiscrete(TD):
    """
    Discrete version of SARSA(lambda) algorithm.

    """
    def __init__(self, policy, mdp_info, learning_rate, lambda_coeff,
                 trace='replacing'):
        """
        Constructor.

        Args:
            lambda_coeff (float): eligibility trace coefficient;
            trace (str, 'replacing'): type of eligibility trace to use.

        """
        self.Q = Table(mdp_info.size)
        self._lambda = lambda_coeff

        self.e = EligibilityTrace(self.Q.shape, trace)
        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        q_current = self.Q[state, action]

        self.next_action = self.draw_action(next_state)
        q_next = self.Q[next_state, self.next_action] if not absorbing else 0.

        delta = reward + self.mdp_info.gamma * q_next - q_current
        self.e.update(state, action)

        self.Q.table += self.alpha(state, action) * delta * self.e.table
        self.e.table *= self.mdp_info.gamma * self._lambda

    def episode_start(self):
        self.e.reset()


class SARSALambdaContinuous(TD):
    """
    Continuous version of SARSA(lambda) algorithm.

    """
    def __init__(self, approximator, policy, mdp_info, learning_rate,
                 lambda_coeff, features, approximator_params=None):
        """
        Constructor.

        Args:
            lambda_coeff (float): eligibility trace coefficient.

        """
        self._approximator_params = dict() if approximator_params is None else \
            approximator_params

        self.Q = Regressor(approximator, **self._approximator_params)
        self.e = np.zeros(self.Q.weights_size)
        self._lambda = lambda_coeff

        super().__init__(self.Q, policy, mdp_info, learning_rate, features)

    def _update(self, state, action, reward, next_state, absorbing):
        phi_state = self.phi(state)
        q_current = self.Q.predict(phi_state, action)

        alpha = self.alpha(state, action)

        self.e = self.mdp_info.gamma * self._lambda * self.e + self.Q.diff(
            phi_state, action)

        self.next_action = self.draw_action(next_state)
        phi_next_state = self.phi(next_state)
        q_next = self.Q.predict(phi_next_state,
                                self.next_action) if not absorbing else 0.

        delta = reward + self.mdp_info.gamma * q_next - q_current

        theta = self.Q.get_weights()
        theta += alpha * delta * self.e
        self.Q.set_weights(theta)

    def episode_start(self):
        self.e = np.zeros(self.Q.weights_size)


class ExpectedSARSA(TD):
    """
    Expected SARSA algorithm.
    "A theoretical and empirical analysis of Expected Sarsa". Seijen H. V. et
    al.. 2009.

    """
    def __init__(self, policy, mdp_info, learning_rate):
        self.Q = Table(mdp_info.size)
        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        q_current = self.Q[state, action]

        if not absorbing:
            q_next = self.Q[next_state, :].dot(self.policy(next_state))
        else:
            q_next = 0.

        self.Q[state, action] = q_current + self.alpha(state, action) * (
            reward + self.mdp_info.gamma * q_next - q_current)


class TrueOnlineSARSALambda(TD):
    """
    True Online SARSA(lambda) with linear function approximation.
    "True Online TD(lambda)". Seijen H. V. et al.. 2014.

    """
    def __init__(self, policy, mdp_info, learning_rate, lambda_coeff,
                 features, approximator_params=None):
        """
        Constructor.

        Args:
            lambda_coeff (float): eligibility trace coefficient.

        """
        self._approximator_params = dict() if approximator_params is None else \
            approximator_params

        self.Q = Regressor(LinearApproximator, **self._approximator_params)
        self.e = np.zeros(self.Q.weights_size)
        self._lambda = lambda_coeff
        self._q_old = None

        super().__init__(self.Q, policy, mdp_info, learning_rate, features)

    def _update(self, state, action, reward, next_state, absorbing):
        phi_state = self.phi(state)
        phi_state_action = get_action_features(phi_state, action,
                                               self.mdp_info.action_space.n)
        q_current = self.Q.predict(phi_state, action)

        if self._q_old is None:
            self._q_old = q_current

        alpha = self.alpha(state, action)

        e_phi = self.e.dot(phi_state_action)
        self.e = self.mdp_info.gamma * self._lambda * self.e + alpha * (
            1. - self.mdp_info.gamma * self._lambda * e_phi) * phi_state_action

        self.next_action = self.draw_action(next_state)
        phi_next_state = self.phi(next_state)
        q_next = self.Q.predict(phi_next_state,
                                self.next_action) if not absorbing else 0.

        delta = reward + self.mdp_info.gamma * q_next - self._q_old

        theta = self.Q.get_weights()
        theta += delta * self.e + alpha * (
            self._q_old - q_current) * phi_state_action
        self.Q.set_weights(theta)

        self._q_old = q_next

    def episode_start(self):
        self._q_old = None
        self.e = np.zeros(self.Q.weights_size)


class RLearning(TD):
    """
    R-Learning algorithm.
    "A Reinforcement Learning Method for Maximizing Undiscounted Rewards".
    Schwartz A.. 1993.

    """
    def __init__(self, policy, mdp_info, learning_rate, beta):
        """
        Constructor.

        Args:
            beta (Parameter): beta coefficient.

        """
        self.Q = Table(mdp_info.size)
        self._rho = 0.
        self.beta = beta

        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        q_current = self.Q[state, action]
        q_next = np.max(self.Q[next_state, :]) if not absorbing else 0.
        delta = reward - self._rho + q_next - q_current
        q_new = q_current + self.alpha(state, action) * delta

        self.Q[state, action] = q_new

        q_max = np.max(self.Q[state, :])
        if q_new == q_max:
            delta = reward + q_next - q_max - self._rho
            self._rho += self.beta(state, action) * delta


class RQLearning(TD):
    """
    RQ-Learning algorithm.
    "Exploiting Structure and Uncertainty of Bellman Updates in Markov Decision
    Processes". Tateo D. et al.. 2017.

    """
    def __init__(self, policy, mdp_info, learning_rate, off_policy=False,
                 beta=None, delta=None):
        """
        Constructor.

        Args:
            off_policy (bool, False): whether to use the off policy setting or
                the online one;
            beta (Parameter, None): beta coefficient;
            delta (Parameter, None): delta coefficient.

        """
        self.off_policy = off_policy
        if delta is not None and beta is None:
            self.delta = delta
            self.beta = None
        elif delta is None and beta is not None:
            self.delta = None
            self.beta = beta
        else:
            raise ValueError('delta or beta parameters needed.')

        self.Q = Table(mdp_info.size)
        self.Q_tilde = Table(mdp_info.size)
        self.R_tilde = Table(mdp_info.size)
        super().__init__(self.Q, policy, mdp_info, learning_rate)

    def _update(self, state, action, reward, next_state, absorbing):
        alpha = self.alpha(state, action, target=reward)
        self.R_tilde[state, action] += alpha * (reward - self.R_tilde[
            state, action])

        if not absorbing:
            q_next = self._next_q(next_state)

            if self.delta is not None:
                beta = alpha * self.delta(state, action, target=q_next,
                                          factor=alpha)
            else:
                beta = self.beta(state, action, target=q_next)

            self.Q_tilde[state, action] += beta * (q_next - self.Q_tilde[
                state, action])

        self.Q[state, action] = self.R_tilde[
            state, action] + self.mdp_info.gamma * self.Q_tilde[state, action]

    def _next_q(self, next_state):
        """
        Args:
            next_state (np.ndarray): the state where next action has to be
                evaluated.

        Returns:
            The weighted estimator value in 'next_state'.

        """
        if self.off_policy:
            return np.max(self.Q[next_state, :])
        else:
            self.next_action = self.draw_action(next_state)

            return self.Q[next_state, self.next_action]
