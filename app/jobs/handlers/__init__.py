from app.jobs.handlers.history_sync import run as run_history_sync
from app.jobs.handlers.profile_rebuild import run as run_profile_rebuild
from app.jobs.handlers.model_train import run as run_model_train

HANDLERS = {
    "history_sync": run_history_sync,
    "profiles_rebuild": run_profile_rebuild,
    "models_train": run_model_train,
}
