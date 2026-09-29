import random
import numpy as np
from dataclasses import dataclass


# ============================================================
# CONFIGURATION
# ============================================================

N_CASTLES = 10
SOLDIERS = 100
N_GAMES = 50_000

# Base castle values.
BASE_VALUES = np.array([
    10, 10, 10, 10, 10,
    10, 10, 10, 10, 10
], dtype=float)


# ============================================================
# BASIC UTILITIES
# ============================================================

def random_composition(total, n):
    """
    Generate a random integer allocation summing exactly to total.
    """
    cuts = sorted(random.sample(range(total + n - 1), n - 1))

    parts = []
    previous = -1

    for c in cuts + [total + n - 1]:
        parts.append(c - previous - 1)
        previous = c

    return np.array(parts, dtype=int)


def normalize_to_budget(x, budget):
    """
    Convert arbitrary non-negative weights into integer allocations
    summing exactly to budget.
    """
    x = np.maximum(np.asarray(x, dtype=float), 0)

    if x.sum() == 0:
        return random_composition(budget, len(x))

    raw = x / x.sum() * budget
    allocation = np.floor(raw).astype(int)

    remainder = budget - allocation.sum()

    if remainder > 0:
        fractions = raw - allocation
        indices = np.argsort(fractions)[::-1]

        for i in indices[:remainder]:
            allocation[i] += 1

    return allocation


def payoff(my_allocation, opp_allocation, values):
    """
    Standard Blotto payoff.

    Win castle -> receive full castle value.
    Lose -> 0.
    Tie -> half value.
    """
    score = 0

    for i in range(len(values)):
        if my_allocation[i] > opp_allocation[i]:
            score += values[i]
        elif my_allocation[i] == opp_allocation[i]:
            score += values[i] / 2

    return score


# ============================================================
# OPPONENT STRATEGIES
# ============================================================

def uniform_strategy(values, budget):
    """
    Spread soldiers approximately evenly.
    """
    weights = np.ones(len(values))
    return normalize_to_budget(weights, budget)


def random_strategy(values, budget):
    """
    Completely random composition.
    """
    return random_composition(budget, len(values))


def proportional_strategy(values, budget):
    """
    Allocate proportional to battlefield value.
    """
    return normalize_to_budget(values, budget)


def concentrated_strategy(values, budget):
    """
    Concentrate heavily on the most valuable castles.

    This represents a simple 'Choker'-style player.
    """
    n = len(values)

    # Pick 3 valuable castles.
    important = np.argsort(values)[-3:]

    weights = np.ones(n) * 0.2
    weights[important] = 4.0

    return normalize_to_budget(weights, budget)


def noisy_concentrated_strategy(values, budget):
    """
    Concentrated strategy with randomness.
    """
    n = len(values)

    important = np.argsort(values)[-3:]

    weights = np.random.exponential(1.0, n)

    weights[important] += np.random.uniform(3, 8, len(important))

    return normalize_to_budget(weights, budget)


def value_squared_strategy(values, budget):
    """
    Overweight high-value castles.
    """
    weights = values ** 2
    return normalize_to_budget(weights, budget)


def random_cluster_strategy(values, budget):
    """
    Pick a random cluster of castles and concentrate resources there.
    """
    n = len(values)

    cluster_size = random.randint(2, 4)

    start = random.randint(0, n - cluster_size)

    cluster = list(range(start, start + cluster_size))

    weights = np.ones(n) * 0.15

    for i in cluster:
        weights[i] = random.uniform(3, 7)

    return normalize_to_budget(weights, budget)


def anti_cluster_strategy(values, budget):
    """
    Deliberately spread emphasis over non-adjacent castles.

    Useful as an 'Innovator'-style strategy.
    """
    n = len(values)

    indices = list(range(n))
    random.shuffle(indices)

    selected = indices[:3]

    weights = np.ones(n) * 0.15

    for i in selected:
        weights[i] = random.uniform(3, 7)

    return normalize_to_budget(weights, budget)


# ============================================================
# MULTIPLIER EVENTS
# ============================================================

def generate_multipliers(n):
    """
    Generate a random multiplier event.

    Most rounds are normal.
    Some rounds have a 2x or 3x castle.
    """
    multipliers = np.ones(n)

    event = random.random()

    if event < 0.60:
        # Normal round.
        return multipliers

    elif event < 0.85:
        # One 2x castle.
        i = random.randrange(n)
        multipliers[i] = 2

    else:
        # One 3x castle.
        i = random.randrange(n)
        multipliers[i] = 3

    return multipliers


# ============================================================
# STRATEGY REGISTRY
# ============================================================

STRATEGIES = {
    "uniform": uniform_strategy,
    "random": random_strategy,
    "proportional": proportional_strategy,
    "concentrated": concentrated_strategy,
    "noisy_concentrated": noisy_concentrated_strategy,
    "value_squared": value_squared_strategy,
    "random_cluster": random_cluster_strategy,
    "anti_cluster": anti_cluster_strategy,
}


# ============================================================
# SIMULATION
# ============================================================

@dataclass
class Results:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    score: float = 0
    opponent_score: float = 0


def simulate_game(my_strategy, opponent_strategy, verbose=False):

    multipliers = generate_multipliers(N_CASTLES)

    values = BASE_VALUES * multipliers

    my_allocation = my_strategy(values, SOLDIERS)

    opp_allocation = opponent_strategy(values, SOLDIERS)

    my_score = payoff(
        my_allocation,
        opp_allocation,
        values
    )

    opp_score = payoff(
        opp_allocation,
        my_allocation,
        values
    )

    if verbose:
        print("\nVALUES")
        print(values)

        print("\nYOU")
        print(my_allocation)

        print("\nOPPONENT")
        print(opp_allocation)

        print("\nSCORE")
        print(my_score, "-", opp_score)

    return my_score, opp_score


def run_simulation(
    my_strategy_name,
    opponent_strategy_name,
    n_games=N_GAMES
):

    my_strategy = STRATEGIES[my_strategy_name]
    opponent_strategy = STRATEGIES[opponent_strategy_name]

    result = Results()

    for _ in range(n_games):

        my_score, opp_score = simulate_game(
            my_strategy,
            opponent_strategy
        )

        result.score += my_score
        result.opponent_score += opp_score

        if my_score > opp_score:
            result.wins += 1

        elif my_score < opp_score:
            result.losses += 1

        else:
            result.ties += 1

    return result


# ============================================================
# HEAD-TO-HEAD TEST
# ============================================================

def compare_strategies(my_strategy):

    print("\n" + "=" * 70)
    print(f"TESTING: {my_strategy}")
    print("=" * 70)

    for opponent in STRATEGIES:

        if opponent == my_strategy:
            continue

        result = run_simulation(
            my_strategy,
            opponent
        )

        win_rate = result.wins / N_GAMES

        avg_score = result.score / N_GAMES

        avg_opp_score = result.opponent_score / N_GAMES

        print(
            f"{opponent:22s} "
            f"Win: {win_rate:6.2%} "
            f"Score: {avg_score:6.2f} "
            f"Opp: {avg_opp_score:6.2f}"
        )


# ============================================================
# ROUND-ROBIN
# ============================================================

def tournament(n_games=10_000):

    strategies = list(STRATEGIES.keys())

    scores = {
        s: 0
        for s in strategies
    }

    wins = {
        s: 0
        for s in strategies
    }

    games = {
        s: 0
        for s in strategies
    }

    for s1 in strategies:

        for s2 in strategies:

            if s1 == s2:
                continue

            result = run_simulation(
                s1,
                s2,
                n_games
            )

            scores[s1] += result.score
            wins[s1] += result.wins
            games[s1] += n_games

    print("\n" + "=" * 70)
    print("TOURNAMENT RESULTS")
    print("=" * 70)

    ranking = []

    for s in strategies:

        avg_score = scores[s] / games[s]

        win_rate = wins[s] / games[s]

        ranking.append(
            (avg_score, win_rate, s)
        )

    ranking.sort(reverse=True)

    for rank, (score, win_rate, name) in enumerate(
        ranking,
        1
    ):

        print(
            f"{rank:2d}. "
            f"{name:22s} "
            f"Avg score={score:7.2f} "
            f"Win rate={win_rate:6.2%}"
        )


# ============================================================
# ADAPTIVE / CROWD STRATEGY
# ============================================================

def expected_win_probability(
    allocation,
    opponent_strategy,
    values,
    simulations=2000
):
    """
    Estimate probability that a fixed allocation beats
    an opponent strategy.
    """

    wins = 0

    for _ in range(simulations):

        opponent = opponent_strategy(
            values,
            SOLDIERS
        )

        my_score = payoff(
            allocation,
            opponent,
            values
        )

        opp_score = payoff(
            opponent,
            allocation,
            values
        )

        if my_score > opp_score:
            wins += 1

        elif my_score == opp_score:
            wins += 0.5

    return wins / simulations


def search_best_response(
    opponent_strategy,
    values,
    candidate_count=5000
):
    """
    Search randomly generated allocations and return
    the allocation with highest simulated win probability.
    """

    best_allocation = None
    best_probability = -1

    for _ in range(candidate_count):

        allocation = random_composition(
            SOLDIERS,
            N_CASTLES
        )

        p = expected_win_probability(
            allocation,
            opponent_strategy,
            values,
            simulations=100
        )

        if p > best_probability:

            best_probability = p
            best_allocation = allocation.copy()

    return best_allocation, best_probability


# ============================================================
# EXAMPLE: BEST RESPONSE TO A CHOKER
# ============================================================

def demonstrate_best_response():

    multipliers = np.ones(N_CASTLES)

    # Create an interesting multiplier event.
    multipliers[5] = 3
    multipliers[2] = 2

    values = BASE_VALUES * multipliers

    print("\nVALUES:")
    print(values)

    allocation, probability = search_best_response(
        concentrated_strategy,
        values,
        candidate_count=3000
    )

    print("\nBEST RESPONSE TO CONCENTRATED PLAYER:")

    print("Allocation:")
    print(allocation)

    print("Estimated win probability:")
    print(f"{probability:.2%}")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    # Example individual matchup.
    compare_strategies("anti_cluster")

    # Full tournament.
    tournament(n_games=2000)

    # Find a best response to a concentrated player.
    demonstrate_best_response()