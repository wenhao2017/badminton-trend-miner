from app.jobs.job_service import JobContext

def run(payload: dict, context: JobContext) -> dict:
    from app.db.session import SessionLocal
    from app.db.models import Player, PlayerProfile, PlayerMetricDistribution, MatchPlayer, Match, MatchPlayerStat
    from app.services.player_profile_builder import PlayerProfileBuilder
    
    db = SessionLocal()
    try:
        players = db.query(Player).all()
        total_players = len(players)
        context.log(f"Rebuilding profiles for {total_players} players.")
        
        builder = PlayerProfileBuilder()
        surfaces = ["all", "hard", "clay", "grass", "indoor"]
        rebuilt_count = 0
        
        for idx, player in enumerate(players):
            if context.should_cancel():
                context.log("Job was cancelled by user.")
                break
                
            context.update_progress(
                current=idx,
                total=total_players,
                message=f"Rebuilding profiles for player: {player.name} ({idx + 1}/{total_players})"
            )
            
            # Execute the profile builder logic for a single player
            db.query(PlayerProfile).filter(PlayerProfile.player_id == player.id).delete()
            db.query(PlayerMetricDistribution).filter(PlayerMetricDistribution.player_id == player.id).delete()

            match_players = db.query(MatchPlayer).filter(MatchPlayer.player_id == player.id).all()
            match_ids = [mp.match_id for mp in match_players]

            if not match_ids:
                continue

            all_player_matches = db.query(Match).filter(Match.id.in_(match_ids)).all()
            all_player_stats = db.query(MatchPlayerStat).filter(MatchPlayerStat.player_id == player.id).all()
            stats_by_match = {s.match_id: s for s in all_player_stats}

            for surface in surfaces:
                if surface == "all":
                    stmt_matches = all_player_matches
                else:
                    stmt_matches = [m for m in all_player_matches if builder._surface_key(m) == surface]

                if not stmt_matches:
                    continue

                won_m_ids = [m.id for m in stmt_matches if m.winner_player_id == player.id]
                lost_m_ids = [m.id for m in stmt_matches if m.loser_player_id == player.id]

                win_stats = [stats_by_match[m_id] for m_id in won_m_ids if m_id in stats_by_match]
                loss_stats = [stats_by_match[m_id] for m_id in lost_m_ids if m_id in stats_by_match]

                sample_size = len(win_stats) + len(loss_stats)
                if sample_size == 0:
                    continue

                win_avgs = builder._calculate_averages(win_stats)
                loss_avgs = builder._calculate_averages(loss_stats)

                profile = builder.profile_repo.get_by_player_and_surface(db, player.id, surface)
                if not profile:
                    profile = PlayerProfile(
                        player_id=player.id,
                        surface=surface,
                        sample_matches=sample_size
                    )
                    db.add(profile)
                
                profile.sample_matches = sample_size
                profile.win_avg_first_serve_in_rate = win_avgs["first_serve_in_rate"]
                profile.win_avg_first_serve_win_rate = win_avgs["first_serve_win_rate"]
                profile.win_avg_second_serve_win_rate = win_avgs["second_serve_win_rate"]
                profile.win_avg_service_points_win_rate = win_avgs["service_points_win_rate"]
                profile.win_avg_return_points_win_rate = win_avgs["return_points_win_rate"]
                profile.win_avg_break_point_convert_rate = win_avgs["break_point_convert_rate"]
                profile.win_avg_break_point_save_rate = win_avgs["break_point_save_rate"]
                profile.win_avg_total_points_win_rate = win_avgs["total_points_win_rate"]

                profile.loss_avg_first_serve_in_rate = loss_avgs["first_serve_in_rate"]
                profile.loss_avg_first_serve_win_rate = loss_avgs["first_serve_win_rate"]
                profile.loss_avg_second_serve_win_rate = loss_avgs["second_serve_win_rate"]
                profile.loss_avg_service_points_win_rate = loss_avgs["service_points_win_rate"]
                profile.loss_avg_return_points_win_rate = loss_avgs["return_points_win_rate"]
                profile.loss_avg_break_point_convert_rate = loss_avgs["break_point_convert_rate"]
                profile.loss_avg_break_point_save_rate = loss_avgs["break_point_save_rate"]
                profile.loss_avg_total_points_win_rate = loss_avgs["total_points_win_rate"]

                db.add(profile)
                builder._write_metric_distributions(db, player.id, surface, win_stats, "win")
                builder._write_metric_distributions(db, player.id, surface, loss_stats, "loss")

            rebuilt_count += 1
            if idx % 10 == 0:
                db.commit()

        db.commit()
        context.update_progress(
            current=total_players,
            total=total_players,
            message=f"Rebuilt profiles for {rebuilt_count} players."
        )
        return {"rebuilt_players_count": rebuilt_count}
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
