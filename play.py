import numpy
import torch

import self_play
import models

class SelfPlay:
    """
    Class which run in a dedicated thread to play games and save them to the replay-buffer.
    """

    def __init__(self, initial_checkpoint, Game, config, seed):
        self.config = config
        self.game = Game(seed)

        # Fix random generator seed
        numpy.random.seed(seed)
        torch.manual_seed(seed)

        # Initialize the network
        self.model = models.MuZeroNetwork(self.config)
        self.model.set_weights(initial_checkpoint["weights"])
        self.model.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
        self.model.eval()
        
        
    def play_game(
        self, temperature, temperature_threshold, render, opponent, muzero_player
    ):
        """
        Play one game with actions based on the Monte Carlo tree search at each moves.
        """
        game_history = self_play.GameHistory()
        observation = self.game.reset()
        game_history.action_history.append(0)
        game_history.observation_history.append(observation)
        game_history.reward_history.append(0)
        game_history.to_play_history.append(self.game.to_play())

        done = False

        if render:
            self.game.render()

        with torch.no_grad():
            while (
                not done and len(game_history.action_history) <= self.config.max_moves
            ):
                assert (
                    len(numpy.array(observation).shape) == 3
                ), f"Observation should be 3 dimensionnal instead of {len(numpy.array(observation).shape)} dimensionnal. Got observation of shape: {numpy.array(observation).shape}"
                assert (
                    numpy.array(observation).shape == self.config.observation_shape
                ), f"Observation should match the observation_shape defined in MuZeroConfig. Expected {self.config.observation_shape} but got {numpy.array(observation).shape}."
                stacked_observations = game_history.get_stacked_observations(
                    -1,
                    self.config.stacked_observations,
                )

                # Choose the action
                if opponent == "self" or muzero_player == self.game.to_play():
                    root, mcts_info = self_play.MCTS(self.config).run(
                        self.model,
                        stacked_observations,
                        self.game.legal_actions(),
                        self.game.to_play(),
                        True,
                    )
                    action = self.select_action(
                        root,
                        temperature
                        if not temperature_threshold
                        or len(game_history.action_history) < temperature_threshold
                        else 0,
                    )

                    if render:
                        print(f'Tree depth: {mcts_info["max_tree_depth"]}')
                        print(
                            f"Root value for player {self.game.to_play()}: {root.value():.2f}"
                        )
                else:
                    action, root = self.select_opponent_action(
                        opponent, stacked_observations
                    )

                observation, reward, done = self.game.step(action)

                if render:
                    print(f"Played action: {self.game.action_to_string(action)}")
                    self.game.render()

                game_history.store_search_statistics(root, self.config.action_space)

                # Next batch
                game_history.action_history.append(action)
                game_history.observation_history.append(observation)
                game_history.reward_history.append(reward)
                game_history.to_play_history.append(self.game.to_play())

        return game_history

    def close_game(self):
        self.game.close()

    def select_opponent_action(self, opponent, stacked_observations):
        """
        Select opponent action for evaluating MuZero level.
        """
        if opponent == "human":
            root, mcts_info = self_play.MCTS(self.config).run(
                self.model,
                stacked_observations,
                self.game.legal_actions(),
                self.game.to_play(),
                True,
            )
            print(f'Tree depth: {mcts_info["max_tree_depth"]}')
            print(f"Root value for player {self.game.to_play()}: {root.value():.2f}")
            print(
                f"Player {self.game.to_play()} turn. MuZero suggests {self.game.action_to_string(self.select_action(root, 0))}"
            )
            return self.game.human_to_action(), root
        elif opponent == "expert":
            return self.game.expert_agent(), None
        elif opponent == "random":
            assert (
                self.game.legal_actions()
            ), f"Legal actions should not be an empty array. Got {self.game.legal_actions()}."
            assert set(self.game.legal_actions()).issubset(
                set(self.config.action_space)
            ), "Legal actions should be a subset of the action space."

            return numpy.random.choice(self.game.legal_actions()), None
        else:
            raise NotImplementedError(
                'Wrong argument: "opponent" argument should be "self", "human", "expert" or "random"'
            )

    @staticmethod
    def select_action(node, temperature):
        """
        Select action according to the visit count distribution and the temperature.
        The temperature is changed dynamically with the visit_softmax_temperature function
        in the config.
        """
        visit_counts = numpy.array(
            [child.visit_count for child in node.children.values()], dtype="int32"
        )
        actions = [action for action in node.children.keys()]
        if temperature == 0:
            action = actions[numpy.argmax(visit_counts)]
        elif temperature == float("inf"):
            action = numpy.random.choice(actions)
        else:
            # See paper appendix Data Generation
            visit_count_distribution = visit_counts ** (1 / temperature)
            visit_count_distribution = visit_count_distribution / sum(
                visit_count_distribution
            )
            action = numpy.random.choice(actions, p=visit_count_distribution)

        return action
