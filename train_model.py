"""
Training pipeline.
Loads scraped GRID data, extracts features, trains the XGBoost model.

Usage:
    python scrape_history.py --months 3    # First: scrape data
    python train_model.py                  # Then: train model
"""

import os
import json
import glob
from typing import List

import config
from feature_extractor import extract_features_from_grid_state, RoundFeatures
from model import MapWinModel


def load_all_series(data_dir: str = None) -> List[dict]:
    """Load all scraped series JSON files."""
    data_dir = data_dir or os.path.join(config.DATA_DIR, "series")
    pattern = os.path.join(data_dir, "*.json")
    files = glob.glob(pattern)

    print(f"Found {len(files)} series files in {data_dir}")

    series = []
    for filepath in files:
        with open(filepath) as f:
            try:
                series.append(json.load(f))
            except json.JSONDecodeError:
                print(f"  Skipping corrupt file: {filepath}")
    return series


def extract_all_features(series_list: List[dict]) -> List[RoundFeatures]:
    """Extract training features from all series."""
    all_features = []
    skipped = 0

    for i, state in enumerate(series_list):
        if i % 200 == 0:
            print(f"  Processing series {i}/{len(series_list)}...")

        games = state.get("games", [])
        for game_idx in range(len(games)):
            try:
                features = extract_features_from_grid_state(
                    state, game_idx=game_idx)
                # Only include rounds where we know the outcome
                labeled = [f for f in features if f.team_a_won_map is not None]
                all_features.extend(labeled)
            except Exception as e:
                skipped += 1
                if skipped <= 10:
                    print(f"  Error on series {state.get('id')}, "
                          f"game {game_idx}: {e}")

    print(f"\nExtracted {len(all_features)} training samples "
          f"({skipped} games skipped due to errors)")
    return all_features


def main():
    print("=" * 60)
    print("CS2 Map Win Probability Model — Training Pipeline")
    print("=" * 60)

    # Step 1: Load data
    print("\n[1/3] Loading scraped series data...")
    series = load_all_series()
    if not series:
        print("No data found! Run scrape_history.py first.")
        print(f"  Expected data in: {os.path.join(config.DATA_DIR, 'series')}")
        return

    # Step 2: Extract features
    print("\n[2/3] Extracting features...")
    features = extract_all_features(series)
    if not features:
        print("No features extracted! Check data quality.")
        return

    # Print some stats
    maps_by_name = {}
    for f in features:
        maps_by_name.setdefault(f.map_name, 0)
        maps_by_name[f.map_name] += 1

    print("\nSamples per map:")
    for name, count in sorted(maps_by_name.items(), key=lambda x: -x[1]):
        print(f"  {name}: {count:,}")

    # Step 3: Train model
    print("\n[3/3] Training XGBoost model...")
    model = MapWinModel()
    model.train(features)

    print("\n" + "=" * 60)
    print("Training complete! Model saved.")
    print(f"  Model path: {model.model_path}")
    print(f"  Training samples: {len(features):,}")
    print("=" * 60)


if __name__ == "__main__":
    main()
