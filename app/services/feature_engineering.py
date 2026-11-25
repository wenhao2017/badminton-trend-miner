class FeatureExtractor:
    feature_columns = [
        "scoring_target", "game_no", "games_lead", "game_score_fraction",
        "point_margin_fraction", "is_deciding_game", "prior_match_win_rate",
        "opponent_prior_match_win_rate", "prior_rally_win_rate",
        "total_rally_win_rate", "recent_8_rallies_win_rate",
        "recent_16_rallies_win_rate", "recent_rally_deviation", "signed_streak",
        "pressure_rally_win_rate",
    ]

    def extract_features(self, snapshot):
        features = (snapshot.raw_snapshot_json or {}).get("features", {})
        return {column: float(features[column]) for column in self.feature_columns}

