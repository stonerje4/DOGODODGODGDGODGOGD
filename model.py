"""
Win probability model.

XGBoost model that predicts P(Team A wins map) given current round state.
Trained on historical GRID data with reconstructed economy.

Also contains the Bo3 series probability calculator that derives
all Polymarket market prices from a single map win probability.
"""

import os
import json
import pickle
import numpy as np
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass

import config
from feature_extractor import RoundFeatures, features_to_dict


@dataclass
class ModelPrediction:
    """Full prediction output for one moment in a match."""
    # Map level
    map_win_prob_a: float      # P(Team A wins current map)

    # Series level (derived)
    series_win_prob_a: float   # P(Team A wins the Bo3/Bo1)
    prob_goes_to_3: float      # P(series goes to map 3)
    prob_a_sweeps: float       # P(Team A wins 2-0)
    prob_b_sweeps: float       # P(Team B wins 2-0)

    # Context
    team_a: str
    team_b: str
    map_name: str
    round_num: int
    series_score_a: int        # Maps won by A
    series_score_b: int        # Maps won by B


class MapWinModel:
    """XGBoost model for map win probability."""

    def __init__(self, model_path: str = None):
        self.model = None
        self.feature_names = None
        self.model_path = model_path or os.path.join(
            config.MODEL_DIR, "map_win_xgb.pkl")

    def train(self, features: List[RoundFeatures]):
        """
        Train the model on historical round data.
        Each RoundFeatures has a target: team_a_won_map
        """
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("pip install xgboost")

        # Convert to feature dicts
        X_dicts = [features_to_dict(f) for f in features
                   if f.team_a_won_map is not None]
        y = [1 if f.team_a_won_map else 0 for f in features
             if f.team_a_won_map is not None]

        if not X_dicts:
            raise ValueError("No labeled training data")

        self.feature_names = sorted(X_dicts[0].keys())
        X = np.array([[d[k] for k in self.feature_names] for d in X_dicts])
        y = np.array(y)

        print(f"Training on {len(X)} samples, {len(self.feature_names)} features")
        print(f"  Positive rate: {y.mean():.3f}")

        # Train/val split (time-based would be better but this works for now)
        split = int(len(X) * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        dtrain = xgb.DMatrix(X_train, label=y_train,
                              feature_names=self.feature_names)
        dval = xgb.DMatrix(X_val, label=y_val,
                            feature_names=self.feature_names)

        params = {
            "objective": "binary:logistic",
            "eval_metric": ["logloss", "auc"],
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 10,
            "seed": 42,
        }

        self.model = xgb.train(
            params,
            dtrain,
            num_boost_round=500,
            evals=[(dtrain, "train"), (dval, "val")],
            early_stopping_rounds=30,
            verbose_eval=50,
        )

        # Print feature importance
        importance = self.model.get_score(importance_type="gain")
        sorted_imp = sorted(importance.items(), key=lambda x: -x[1])
        print("\nTop 15 features by gain:")
        for name, gain in sorted_imp[:15]:
            print(f"  {name}: {gain:.1f}")

        # Save model
        self.save()

    def predict(self, features: RoundFeatures) -> float:
        """Predict P(Team A wins map) from current round state."""
        if self.model is None:
            self.load()

        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("pip install xgboost")

        d = features_to_dict(features)
        X = np.array([[d[k] for k in self.feature_names]])
        dmatrix = xgb.DMatrix(X, feature_names=self.feature_names)
        return float(self.model.predict(dmatrix)[0])

    def save(self):
        """Save model to disk."""
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "feature_names": self.feature_names,
            }, f)
        print(f"Model saved to {self.model_path}")

    def load(self):
        """Load model from disk."""
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"No model at {self.model_path}. Run train_model.py first.")
        with open(self.model_path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.feature_names = data["feature_names"]


class SeriesCalculator:
    """
    Derives all series-level probabilities from map win probabilities.
    Pure math — no ML needed.
    """

    @staticmethod
    def bo3_probabilities(
        p_current_map: float,
        p_map2: float,
        p_map3: float,
        series_score_a: int,
        series_score_b: int,
        current_map_in_progress: bool = True,
    ) -> Dict[str, float]:
        """
        Calculate all Bo3 series probabilities.

        Args:
            p_current_map: P(A wins current map) — from model
            p_map2: P(A wins map 2) — from Pinnacle pre-match odds
            p_map3: P(A wins map 3/decider) — estimated
            series_score_a: Maps already won by A
            series_score_b: Maps already won by B
            current_map_in_progress: Is a map currently being played?
        """
        # If series is already decided
        if series_score_a == 2:
            return {"series_win_a": 1.0, "goes_to_3": 0.0,
                    "a_sweeps": 1.0 if series_score_b == 0 else 0.0,
                    "b_sweeps": 0.0}
        if series_score_b == 2:
            return {"series_win_a": 0.0, "goes_to_3": 0.0,
                    "a_sweeps": 0.0,
                    "b_sweeps": 1.0 if series_score_a == 0 else 0.0}

        # Series is 0-0, map 1 in progress
        if series_score_a == 0 and series_score_b == 0:
            p1 = p_current_map if current_map_in_progress else p_map2

            # A wins map1 then...
            # A wins series = A wins M1*M2 + A wins M1 * B wins M2 * A wins M3
            #               + B wins M1 * A wins M2 * A wins M3
            p_a_wins = (p1 * p_map2
                        + p1 * (1 - p_map2) * p_map3
                        + (1 - p1) * p_map2 * p_map3)

            p_goes_3 = p1 * (1 - p_map2) + (1 - p1) * p_map2
            p_a_sweeps = p1 * p_map2
            p_b_sweeps = (1 - p1) * (1 - p_map2)

        # Series is 1-0 (A leads), map 2 in progress
        elif series_score_a == 1 and series_score_b == 0:
            p2 = p_current_map if current_map_in_progress else p_map2
            p_a_wins = p2 + (1 - p2) * p_map3
            p_goes_3 = 1 - p2
            p_a_sweeps = p2
            p_b_sweeps = 0.0

        # Series is 0-1 (B leads), map 2 in progress
        elif series_score_a == 0 and series_score_b == 1:
            p2 = p_current_map if current_map_in_progress else p_map2
            p_a_wins = p2 * p_map3
            p_goes_3 = p2
            p_a_sweeps = 0.0
            p_b_sweeps = 1 - p2

        # Series is 1-1, map 3 in progress
        elif series_score_a == 1 and series_score_b == 1:
            p3 = p_current_map if current_map_in_progress else p_map3
            p_a_wins = p3
            p_goes_3 = 1.0  # Already at map 3
            p_a_sweeps = 0.0
            p_b_sweeps = 0.0

        else:
            raise ValueError(f"Invalid series score: {series_score_a}-{series_score_b}")

        return {
            "series_win_a": p_a_wins,
            "goes_to_3": p_goes_3,
            "a_sweeps": p_a_sweeps,
            "b_sweeps": p_b_sweeps,
        }

    @staticmethod
    def bo1_probabilities(p_map: float) -> Dict[str, float]:
        """Bo1 is trivial — map win = series win."""
        return {
            "series_win_a": p_map,
            "goes_to_3": 0.0,
            "a_sweeps": p_map,
            "b_sweeps": 1 - p_map,
        }
