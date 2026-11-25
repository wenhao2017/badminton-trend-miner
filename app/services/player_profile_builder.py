from collections import defaultdict
from statistics import mean, median, pstdev, quantiles

from sqlalchemy import select

from app.db.models import Match, MatchPlayerStat, PlayerMetricDistribution, PlayerProfile

METRICS = {
    "total_points_win_rate": ("total_points_won", "total_points"),
    "service_points_win_rate": ("service_points_won", "service_points_total"),
    "return_points_win_rate": ("return_points_won", "return_points_total"),
}


class PlayerProfileBuilder:
    def rebuild_profiles(self, db):
        buckets = defaultdict(list)
        stmt = select(MatchPlayerStat, Match.winner_player_id, Match.scoring_target).join(
            Match, Match.id == MatchPlayerStat.match_id).where(Match.timeline_complete.is_(True))
        for stats, winner, target in db.execute(stmt).yield_per(500):
            buckets[stats.player_id, target, "win" if winner == stats.player_id else "loss"].append(
                {key: getattr(stats, numerator) / getattr(stats, denominator)
                 for key, (numerator, denominator) in METRICS.items() if getattr(stats, denominator)}
            )
        db.query(PlayerMetricDistribution).delete()
        db.query(PlayerProfile).delete()
        groups = {(player_id, target) for player_id, target, _ in buckets}
        for player_id, target in groups:
            profile = PlayerProfile(player_id=player_id, surface=f"{target}-point", sample_matches=0)
            for result in ("win", "loss"):
                rows = buckets.get((player_id, target, result), [])
                profile.sample_matches += len(rows)
                for metric in METRICS:
                    values = [row[metric] for row in rows if metric in row]
                    if not values:
                        continue
                    avg = mean(values)
                    setattr(profile, f"{result}_avg_{metric}", avg)
                    percentiles = quantiles(values, n=100, method="inclusive") if len(values) > 1 else values * 99
                    db.add(PlayerMetricDistribution(
                        player_id=player_id, surface=profile.surface, metric_name=metric, result_type=result,
                        sample_count=len(values), avg_value=avg, median_value=median(values),
                        std_value=pstdev(values), p25_value=percentiles[24], p75_value=percentiles[74],
                        p90_value=percentiles[89]))
            db.add(profile)
        db.commit()
        return len(groups)

