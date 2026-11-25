from sqlalchemy.orm import Session
from app.db.models import Player, Match, MatchPlayer, MatchPointEvent, MatchPlayerStat
from app.db.repositories import (
    PlayerRepository, MatchRepository, MatchPlayerRepository,
    MatchPointEventRepository, MatchPlayerStatRepository
)
from app.services.schedule_window import parse_time
from sqlalchemy import delete, select, text
from app.config import settings
from app.services.badminton_timeline import parse_timeline, to_point
from app.utils.badminton_score import target_for_event
from app.db.models import MatchSConfirmation
from app.utils.sportradar_event import get_event_category, get_event_season, get_event_tournament, get_event_tournament_level, get_event_round, get_event_competition_stage, is_womens_singles_event
from app.services.signal_recommendation import (
    provider_match_status, TERMINAL_MATCH_STATUSES, COMPLETED_MATCH_STATUSES,
    PREMATCH_STATUSES, ACTIVE_MATCH_STATUSES,
)
from loguru import logger
from typing import Dict, Any

class SportradarImporter:
    def __init__(self):
        self.player_repo = PlayerRepository()
        self.match_repo = MatchRepository()
        self.match_player_repo = MatchPlayerRepository()
        self.point_repo = MatchPointEventRepository()
        self.stats_repo = MatchPlayerStatRepository()

    def _import_player(self, db: Session, competitor_json: Dict[str, Any]) -> Player:
        """Parses competitor JSON and inserts/updates a Player."""
        ext_id = competitor_json.get("id")
        if not ext_id:
            raise ValueError("Competitor JSON missing 'id'")

        player = self.player_repo.get_by_external_id(db, ext_id)
        name = competitor_json.get("name", "Unknown Player")
        country = competitor_json.get("country")
        hand = competitor_json.get("hand")
        birth_date = competitor_json.get("date_of_birth")

        if not player:
            player = Player(
                external_player_id=ext_id,
                provider="sportradar",
                name=name,
                country=country,
                hand=hand,
                birth_date=birth_date
            )
            db.add(player)
            db.flush()
            logger.info(f"Imported new player: {name} ({ext_id})")
        else:
            player.name = name
            player.country = country or player.country
            player.hand = hand or player.hand
            player.birth_date = birth_date or player.birth_date
            db.flush()
            logger.debug(f"Updated player info: {name} ({ext_id})")
            
        return player

    def import_match(self, db: Session, summary_json: Dict[str, Any]) -> Match:
        """Parses match summary JSON, upserting Match and MatchPlayers."""
        sport_event = summary_json.get("sport_event")
        status_json = summary_json.get("sport_event_status", {})
        
        if not sport_event:
            raise ValueError("Summary JSON missing 'sport_event'")

        ext_match_id = sport_event.get("id")
        if not ext_match_id or not is_womens_singles_event(sport_event):
            raise ValueError("Badminton women’s singles event ID and discipline are required")
        competitors = sport_event.get("competitors") or []
        if ({c.get("qualifier") for c in competitors} != {"home", "away"} or len(competitors) != 2
                or len({c.get("id") for c in competitors if c.get("id")}) != 2):
            raise ValueError("Two distinct player IDs with home/away assignments are required")

        tournament_json = get_event_tournament(sport_event)
        season_json = get_event_season(sport_event)
        category_json = get_event_category(sport_event)
        tournament_name = (
            tournament_json.get("name")
            or season_json.get("name")
            or category_json.get("name")
            or "Unknown Tournament"
        )
        tournament_id = (
            tournament_json.get("id")
            or season_json.get("id")
            or category_json.get("id")
            or "unknown_tournament"
        )
        tournament_level = get_event_tournament_level(sport_event)

        start = parse_time(sport_event.get("start_time"))
        if start is None:
            raise ValueError("Match start time missing or invalid")
        match_date = start.replace(tzinfo=None)
        indoor, surface = True, "indoor"
        target = settings.SCORING_TARGET_OVERRIDE or target_for_event(match_date.date())
        match_round = get_event_round(sport_event)
        competition_stage = get_event_competition_stage(sport_event)
        best_of = 3

        db.execute(text("BEGIN IMMEDIATE"))
        match = self.match_repo.get_by_external_id(db, ext_match_id)
        
        import json
        import hashlib
        from datetime import datetime
        
        status = provider_match_status(status_json)
        if match and (
            (match.status in TERMINAL_MATCH_STATUSES and status not in TERMINAL_MATCH_STATUSES)
            or (match.status in ACTIVE_MATCH_STATUSES and status in PREMATCH_STATUSES)
        ):
            # Feed placeholders can briefly regress to not_started after live play.
            # Preserve the score, winner and hashes as well as the lifecycle state.
            logger.warning(f"Ignoring regressive match update {ext_match_id}: {match.status} -> {status}")
            db.commit()
            return match
        status_str = json.dumps(status_json, sort_keys=True)
        summary_hash = hashlib.sha256(status_str.encode("utf-8")).hexdigest()
        
        final_score = status_json.get("final_score")
        if not final_score and "period_scores" in status_json:
            scores = []
            for ps in sorted(status_json["period_scores"], key=lambda x: x.get("number", 0)):
                h = ps.get("home_score")
                a = ps.get("away_score")
                if h is not None and a is not None:
                    scores.append(f"{h}-{a}")
            if scores:
                final_score = ", ".join(scores)
        winner_id_ext = status_json.get("winner_id")
        if (not winner_id_ext and status in COMPLETED_MATCH_STATUSES
                and not status_json.get("winning_reason")
                and status_json.get("match_status") in (None, "ended", "closed", "completed")):
            # Some final feeds omit winner_id but explicitly report sets won.
            # Never infer a retirement winner from a partial score or a lead.
            home = status_json.get("home_score")
            away = status_json.get("away_score")
            required_sets = int(best_of or 3) // 2 + 1
            if type(home) is int and type(away) is int:
                side = None
                if home == required_sets and 0 <= away < required_sets:
                    side = "home"
                elif away == required_sets and 0 <= home < required_sets:
                    side = "away"
                candidates = [c for c in sport_event.get("competitors", []) if c.get("qualifier") == side]
                if side and len(candidates) == 1:
                    winner_id_ext = candidates[0].get("id")

        if not match:
            match = Match(
                external_match_id=ext_match_id,
                provider="sportradar",
                tournament=tournament_name,
                tournament_id=tournament_id,
                tournament_level=tournament_level,
                match_date=match_date,
                surface=surface,
                indoor=indoor,
                round=match_round,
                competition_stage=competition_stage,
                best_of=best_of,
                scoring_target=target,
                status=status,
                final_score=final_score,
                last_summary_hash=summary_hash,
                last_provider_fetch_at=datetime.utcnow()
            )
            if status in TERMINAL_MATCH_STATUSES:
                match.finalized_at = datetime.utcnow()
            db.add(match)
            db.flush()
            logger.info(f"Imported new match: {tournament_name} - {ext_match_id}")
        else:
            match.tournament = tournament_name or match.tournament
            match.tournament_id = tournament_id or match.tournament_id
            match.status = status
            match.final_score = final_score or match.final_score
            match.tournament_level = tournament_level or match.tournament_level
            match.match_date = match_date or match.match_date
            match.surface = surface or match.surface
            match.indoor = indoor
            match.round = match_round or match.round
            match.competition_stage = competition_stage or match.competition_stage
            match.best_of = best_of or match.best_of
            match.last_summary_hash = summary_hash
            match.last_provider_fetch_at = datetime.utcnow()
            if status in TERMINAL_MATCH_STATUSES:
                if not match.finalized_at:
                    match.finalized_at = datetime.utcnow()
            db.flush()
            logger.debug(f"Updated match status: {ext_match_id} -> {status}")

        # Parse competitors
        db_players = []
        for comp in competitors:
            p = self._import_player(db, comp)
            db_players.append((p, comp.get("qualifier"))) # home / away

        # Assign winner / loser if ended
        winner_id_ext = winner_id_ext if any(p.external_player_id == winner_id_ext for p, _ in db_players) else None
        if winner_id_ext and status in TERMINAL_MATCH_STATUSES:
            winner_player = self.player_repo.get_by_external_id(db, winner_id_ext)
            if winner_player and any(p.id == winner_player.id for p, _ in db_players):
                match.winner_player_id = winner_player.id
                # Determine loser
                loser = next((item[0] for item in db_players if item[0].id != winner_player.id), None)
                if loser:
                    match.loser_player_id = loser.id
                db.flush()

        # Upsert MatchPlayers
        for player, side in db_players:
            opponents = [item[0] for item in db_players if item[0].id != player.id]
            opponent_id = opponents[0].id if opponents else player.id
            
            mp = self.match_player_repo.get_by_match_and_player(db, match.id, player.id)
            
            result = None
            if winner_id_ext and status in TERMINAL_MATCH_STATUSES:
                result = "won" if player.external_player_id == winner_id_ext else "lost"

            # Parse seeds & rankings from competitor structure or fallback
            seed = None
            ranking = None
            for comp in competitors:
                if comp.get("id") == player.external_player_id:
                    seed = comp.get("seed")
                    ranking = comp.get("ranking")

            if not mp:
                mp = MatchPlayer(
                    match_id=match.id,
                    player_id=player.id,
                    opponent_id=opponent_id,
                    side=side or "home",
                    seed=seed,
                    ranking=ranking,
                    result=result
                )
                db.add(mp)
            else:
                mp.result = result or mp.result
                mp.seed = seed or mp.seed
                mp.ranking = ranking or mp.ranking
                mp.side = side
                mp.opponent_id = opponent_id

        db.commit()
        return match


    def import_timeline(self, db: Session, match_id_db: int, timeline_json: Dict[str, Any]) -> int:
        from datetime import datetime
        import hashlib
        import json

        db.execute(text("BEGIN IMMEDIATE"))
        match = db.get(Match, match_id_db, populate_existing=True)
        players = dict(db.execute(select(MatchPlayer.side, MatchPlayer.player_id).where(
            MatchPlayer.match_id == match.id)).all())
        event = timeline_json.get("sport_event") or {}
        if event.get("id") not in {None, match.external_match_id}:
            db.rollback()
            raise ValueError("Timeline event ID does not match")
        try:
            if players.keys() != {"home", "away"}:
                raise ValueError("Timeline requires both competitors")
            rallies = parse_timeline(timeline_json, match.scoring_target, match.best_of)
        except ValueError as exc:
            match.timeline_ready = False
            db.commit()
            logger.warning("Unusable rally data for {}: {}", match.external_match_id, exc)
            return 0

        if len(rallies) < (match.last_point_count or 0):
            match.timeline_ready = False
            db.commit()
            return 0

        points = [to_point(match.id, index, rally, players, match.scoring_target)
                  for index, rally in enumerate(rallies, 1)]
        for point in points:
            point.raw_event_json["home_player_id"] = players["home"]
            point.raw_event_json["away_player_id"] = players["away"]
        raw_hash = hashlib.sha256(json.dumps([p.raw_event_json for p in points], sort_keys=True).encode()).hexdigest()
        previous_count = match.last_point_count or 0
        current_status = provider_match_status(timeline_json.get("sport_event_status") or {})
        match.timeline_ready = bool(points and current_status == "live"
                                    and not (timeline_json.get("sport_event_status") or {}).get("scout_abandoned"))
        last_scores = rallies[-1].games_after if rallies else (0, 0)
        needed = match.best_of // 2 + 1
        terminal_winner = next((players[side] for side, value in zip(("home", "away"), last_scores)
                                if value == needed), None)
        match.timeline_complete = bool(match.status in COMPLETED_MATCH_STATUSES and terminal_winner
                                       and terminal_winner == match.winner_player_id)
        last_time = (points[-1].raw_event_json or {}).get("time") if points else None
        parsed = parse_time(last_time)
        match.last_rally_at = parsed.replace(tzinfo=None) if parsed else None
        match.last_provider_fetch_at = datetime.utcnow()

        if raw_hash != match.last_timeline_hash:
            existing = self.point_repo.get_points_by_match(db, match.id)
            is_append = all(old.raw_event_json == new.raw_event_json for old, new in zip(existing, points))
            if not is_append:
                db.execute(delete(MatchPointEvent).where(MatchPointEvent.match_id == match.id))
                confirmation = db.get(MatchSConfirmation, match.id)
                if confirmation and not confirmation.published_signal_id:
                    confirmation.last_point_index = 0
                    confirmation.state_json = {}
                match.timeline_ready = False
            db.add_all(points[len(existing):] if is_append else points)
            match.last_point_count = len(points)
            match.last_timeline_hash = raw_hash
        if match.timeline_complete:
            self._write_rally_stats(db, match, points, players)
        db.commit()
        return max(len(points) - previous_count, 0)

    def _write_rally_stats(self, db, match, points, players):
        if match.training_source_hash == match.last_timeline_hash:
            return
        for player_id, opponent_id in (tuple(players.values()), tuple(reversed(players.values()))):
            stat = self.stats_repo.get_by_match_and_player(db, match.id, player_id)
            if stat is None:
                stat = MatchPlayerStat(match_id=match.id, player_id=player_id, opponent_id=opponent_id)
                db.add(stat)
            service = [p for p in points if p.raw_event_json["serve_known"] and p.server_player_id == player_id]
            receiving = [p for p in points if p.raw_event_json["serve_known"] and p.receiver_player_id == player_id]
            stat.total_points_won = sum(p.point_winner_player_id == player_id for p in points)
            stat.total_points = len(points)
            stat.service_points_total = len(service)
            stat.service_points_won = sum(p.point_winner_player_id == player_id for p in service)
            stat.return_points_total = len(receiving)
            stat.return_points_won = sum(p.point_winner_player_id == player_id for p in receiving)
            stat.raw_stats_json = {"source": "validated_rallies", "scoring_target": match.scoring_target}
