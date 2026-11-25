from app.jobs.job_service import JobContext

def run(payload: dict, context: JobContext) -> dict:
    from app.services.historical_importer import HistoricalImporter
    from app.config import settings
    from datetime import datetime, timedelta
    from app.db.session import SessionLocal

    if not settings.api_keys_list:
        raise ValueError("Set SPORTRADAR_API_KEYS before syncing badminton history.")
    start_date_str = payload["start_date"]
    end_date_str = payload["end_date"]
    women_only = payload.get("women_only", True)
    force = payload.get("force", False)

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    total_days = (end_date - start_date).days + 1

    importer = HistoricalImporter()
    
    total_processed = 0
    total_success = 0
    total_failed = 0
    total_skipped = 0

    current_date = start_date
    current_day_index = 0

    while current_date <= end_date:
        if context.should_cancel():
            context.log("Job was cancelled by user.")
            break

        date_str = current_date.strftime("%Y-%m-%d")
        context.log(f"Starting import for date: {date_str}")
        context.update_progress(
            current=current_day_index,
            total=total_days,
            message=f"Syncing {date_str} ({current_day_index + 1}/{total_days})"
        )

        db = SessionLocal()
        try:
            day_summary = importer.import_date_range(
                db=db,
                start_date_str=date_str,
                end_date_str=date_str,
                women_only=women_only,
                force=force
            )
            total_processed += day_summary.get("total_matches_found", 0)
            total_success += day_summary.get("imported_successfully", 0)
            total_failed += day_summary.get("failed_imports", 0)
            total_skipped += day_summary.get("skipped_dates_count", 0)
        except Exception as e:
            context.log(f"Error processing {date_str}: {e}")
            total_failed += 1
        finally:
            db.close()

        current_day_index += 1
        current_date += timedelta(days=1)

    result_summary = {
        "start_date": start_date_str,
        "end_date": end_date_str,
        "total_matches_found": total_processed,
        "imported_successfully": total_success,
        "failed_imports": total_failed,
        "skipped_dates_count": total_skipped
    }
    context.update_progress(
        current=total_days,
        total=total_days,
        message=f"Completed. Imported: {total_success}, Failed: {total_failed}, Skipped: {total_skipped}"
    )
    return result_summary
