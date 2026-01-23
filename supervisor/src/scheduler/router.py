import json
import subprocess
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from croniter import croniter

from ..auth.utils import verify_token
from .schemas import ScheduledTask, TaskRun, TaskType, ScheduleFrequency

router = APIRouter(prefix="/scheduler", tags=["scheduler"])

TASKS_FILE = "/data/scheduled_tasks.json"
RUNS_FILE = "/data/task_runs.json"

# In-memory scheduler state
_scheduler_running = False
_scheduler_task: Optional[asyncio.Task] = None


def load_tasks() -> list[ScheduledTask]:
    """Load scheduled tasks from file."""
    path = Path(TASKS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [ScheduledTask(**t) for t in data]
    except Exception:
        return []


def save_tasks(tasks: list[ScheduledTask]) -> None:
    """Save scheduled tasks to file."""
    path = Path(TASKS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([t.model_dump() for t in tasks], indent=2))


def load_runs() -> list[TaskRun]:
    """Load task runs from file."""
    path = Path(RUNS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [TaskRun(**r) for r in data[-100:]]  # Keep last 100
    except Exception:
        return []


def save_run(run: TaskRun) -> None:
    """Save a task run."""
    runs = load_runs()
    # Update existing or append
    for i, r in enumerate(runs):
        if r.id == run.id:
            runs[i] = run
            break
    else:
        runs.append(run)
    runs = runs[-100:]  # Keep last 100
    path = Path(RUNS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump() for r in runs], indent=2))


def get_cron_expression(task: ScheduledTask) -> str:
    """Generate cron expression from task settings."""
    if task.frequency == ScheduleFrequency.CUSTOM and task.cron_expression:
        return task.cron_expression

    minute = task.minute
    hour = task.hour

    if task.frequency == ScheduleFrequency.HOURLY:
        return f"{minute} * * * *"
    elif task.frequency == ScheduleFrequency.DAILY:
        return f"{minute} {hour} * * *"
    elif task.frequency == ScheduleFrequency.WEEKLY:
        dow = task.day_of_week if task.day_of_week is not None else 0
        return f"{minute} {hour} * * {dow}"
    elif task.frequency == ScheduleFrequency.MONTHLY:
        dom = task.day_of_month if task.day_of_month is not None else 1
        return f"{minute} {hour} {dom} * *"

    return f"{minute} {hour} * * *"


def get_next_run(task: ScheduledTask) -> str:
    """Calculate next run time for a task."""
    cron = get_cron_expression(task)
    try:
        iter = croniter(cron, datetime.now())
        return iter.get_next(datetime).isoformat()
    except Exception:
        return ""


async def execute_task(task: ScheduledTask) -> tuple[bool, str]:
    """Execute a scheduled task."""
    try:
        if task.task_type == TaskType.SNAPSHOT:
            if not task.target_pool:
                return False, "No target pool specified"
            # Create snapshot
            name = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            result = subprocess.run(
                ["btrfs", "subvolume", "snapshot", "-r",
                 f"/mnt/pools/{task.target_pool}",
                 f"/mnt/pools/{task.target_pool}/.snapshots/{name}"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, result.stderr
            # Cleanup old snapshots if keep_count is set
            if task.keep_count:
                # List and delete old snapshots
                pass
            return True, f"Snapshot {name} created"

        elif task.task_type == TaskType.SCRUB:
            if not task.target_pool:
                return False, "No target pool specified"
            result = subprocess.run(
                ["btrfs", "scrub", "start", f"/mnt/pools/{task.target_pool}"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, result.stderr
            return True, "Scrub started"

        elif task.task_type == TaskType.SMART_TEST:
            if not task.target_disk:
                return False, "No target disk specified"
            result = subprocess.run(
                ["smartctl", "-t", "short", f"/dev/{task.target_disk}"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, result.stderr
            return True, "SMART test started"

        elif task.task_type == TaskType.CUSTOM:
            if not task.command:
                return False, "No command specified"
            result = subprocess.run(
                task.command, shell=True,
                capture_output=True, text=True
            )
            return result.returncode == 0, result.stdout or result.stderr

        return False, "Unknown task type"

    except Exception as e:
        return False, str(e)


async def run_task(task: ScheduledTask) -> None:
    """Run a task and save the result."""
    run_id = f"run_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    run = TaskRun(
        id=run_id,
        task_id=task.id or "",
        task_name=task.name,
        started_at=datetime.now().isoformat(),
        status="running"
    )
    save_run(run)

    success, output = await execute_task(task)

    run.finished_at = datetime.now().isoformat()
    run.status = "success" if success else "failed"
    if success:
        run.output = output
    else:
        run.error = output
    save_run(run)

    # Update task with last run info
    tasks = load_tasks()
    for t in tasks:
        if t.id == task.id:
            t.last_run = run.started_at
            t.last_status = run.status
            t.next_run = get_next_run(t)
            break
    save_tasks(tasks)


async def scheduler_loop():
    """Main scheduler loop - checks tasks every minute."""
    global _scheduler_running
    while _scheduler_running:
        try:
            tasks = load_tasks()
            now = datetime.now()

            for task in tasks:
                if not task.enabled:
                    continue

                cron = get_cron_expression(task)
                try:
                    iter = croniter(cron, now - timedelta(minutes=1))
                    next_time = iter.get_next(datetime)
                    # If next run is within this minute, execute
                    if next_time <= now < next_time + timedelta(minutes=1):
                        asyncio.create_task(run_task(task))
                except Exception:
                    pass

        except Exception as e:
            print(f"Scheduler error: {e}")

        await asyncio.sleep(60)  # Check every minute


@router.on_event("startup")
async def start_scheduler():
    """Start the scheduler on app startup."""
    global _scheduler_running, _scheduler_task
    _scheduler_running = True
    _scheduler_task = asyncio.create_task(scheduler_loop())


@router.on_event("shutdown")
async def stop_scheduler():
    """Stop the scheduler on app shutdown."""
    global _scheduler_running, _scheduler_task
    _scheduler_running = False
    if _scheduler_task:
        _scheduler_task.cancel()


@router.get("/tasks", response_model=list[ScheduledTask])
async def list_tasks(_: dict = Depends(verify_token)):
    """List all scheduled tasks."""
    tasks = load_tasks()
    # Update next_run for each task
    for task in tasks:
        task.next_run = get_next_run(task)
    return tasks


@router.post("/tasks", response_model=ScheduledTask)
async def create_task(task: ScheduledTask, _: dict = Depends(verify_token)):
    """Create a new scheduled task."""
    tasks = load_tasks()

    task.id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    task.next_run = get_next_run(task)

    tasks.append(task)
    save_tasks(tasks)

    return task


@router.put("/tasks/{task_id}", response_model=ScheduledTask)
async def update_task(task_id: str, task: ScheduledTask, _: dict = Depends(verify_token)):
    """Update a scheduled task."""
    tasks = load_tasks()

    for i, t in enumerate(tasks):
        if t.id == task_id:
            task.id = task_id
            task.next_run = get_next_run(task)
            tasks[i] = task
            save_tasks(tasks)
            return task

    raise HTTPException(404, f"Task not found: {task_id}")


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, _: dict = Depends(verify_token)):
    """Delete a scheduled task."""
    tasks = load_tasks()
    tasks = [t for t in tasks if t.id != task_id]
    save_tasks(tasks)
    return {"status": "deleted", "id": task_id}


@router.post("/tasks/{task_id}/run")
async def run_task_now(task_id: str, background_tasks: BackgroundTasks, _: dict = Depends(verify_token)):
    """Run a task immediately."""
    tasks = load_tasks()

    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")

    background_tasks.add_task(run_task, task)
    return {"status": "started", "task_id": task_id}


@router.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, _: dict = Depends(verify_token)):
    """Enable/disable a task."""
    tasks = load_tasks()

    for task in tasks:
        if task.id == task_id:
            task.enabled = not task.enabled
            save_tasks(tasks)
            return {"status": "toggled", "enabled": task.enabled}

    raise HTTPException(404, f"Task not found: {task_id}")


@router.get("/runs", response_model=list[TaskRun])
async def list_runs(_: dict = Depends(verify_token)):
    """List recent task runs."""
    return load_runs()


@router.get("/runs/{task_id}", response_model=list[TaskRun])
async def list_task_runs(task_id: str, _: dict = Depends(verify_token)):
    """List runs for a specific task."""
    runs = load_runs()
    return [r for r in runs if r.task_id == task_id]
