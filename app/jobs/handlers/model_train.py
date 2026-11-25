from app.jobs.job_service import JobContext

def run(payload: dict, context: JobContext) -> dict:
    from app.db.session import SessionLocal
    from app.services.model_trainer import ModelTrainer
    
    target_label = payload.get("target_label", "label_win_match")
    context.log(f"Starting model training for target_label: {target_label}")
    context.update_progress(current=10, total=100, message="Preparing data...")

    db = SessionLocal()
    try:
        trainer = ModelTrainer()
        if target_label == "all":
            labels = ["label_win_match"]
            result = {}
            for idx, label in enumerate(labels):
                if context.should_cancel():
                    context.log("Job cancelled by user.")
                    break
                context.update_progress(
                    current=20 + idx * 40,
                    total=100,
                    message=f"Training {label} model..."
                )
                model_rec = trainer.train_model(db, label)
                if model_rec:
                    result[label] = {
                        "model_name": model_rec.model_name,
                        "model_path": model_rec.model_path,
                        "metrics": model_rec.metrics_json
                    }
            context.update_progress(current=100, total=100, message="Finished training all models.")
            return result
        else:
            context.update_progress(current=40, total=100, message=f"Fitting model for {target_label}...")
            model_rec = trainer.train_model(db, target_label, scoring_target=payload.get("scoring_target"))
            if not model_rec:
                raise ValueError("Model training did not return a valid record.")
            context.update_progress(current=100, total=100, message="Finished training model.")
            return {
                "model_name": model_rec.model_name,
                "model_path": model_rec.model_path,
                "metrics": model_rec.metrics_json
            }
    finally:
        db.close()
