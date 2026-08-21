"""Dashboard routes — home page with stats and activity feed."""

from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the dashboard page."""
    factory = get_session_factory()
    with factory() as session:
        total_accounts = session.query(Account).count()

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        todays_uploads = (
            session.query(Batch)
            .filter(Batch.status == BatchStatus.DONE, Batch.uploaded_at >= today_start)
            .count()
        )

        pending_batches = session.query(Batch).filter(Batch.status == BatchStatus.PENDING).count()
        failed_batches = session.query(Batch).filter(Batch.status == BatchStatus.FAILED).count()

        accounts = session.query(Account).filter(Account.enabled == True).all()
        account_list = [{"id": a.id, "name": a.name} for a in accounts]

        recent = (
            session.query(ActivityLog)
            .order_by(ActivityLog.timestamp.desc())
            .limit(20)
            .all()
        )
        activity_list = []
        for item in recent:
            account_name = ""
            if item.account_id:
                acct = session.query(Account).filter(Account.id == item.account_id).first()
                account_name = acct.name if acct else ""
            activity_list.append({
                "event_type": item.event_type,
                "message": item.message,
                "timestamp": item.timestamp.strftime("%Y-%m-%d %H:%M") if item.timestamp else "",
                "account_name": account_name,
            })

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "total_accounts": total_accounts,
            "todays_uploads": todays_uploads,
            "pending_batches": pending_batches,
            "failed_batches": failed_batches,
            "accounts": account_list,
            "recent_activity": activity_list,
        }
    )


@router.get("/api/dashboard/stats", response_class=JSONResponse)
async def dashboard_stats():
    factory = get_session_factory()
    with factory() as session:
        total_accounts = session.query(Account).count()
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        todays_uploads = (
            session.query(Batch)
            .filter(Batch.status == BatchStatus.DONE, Batch.uploaded_at >= today_start)
            .count()
        )
        pending_batches = session.query(Batch).filter(Batch.status == BatchStatus.PENDING).count()
        failed_batches = session.query(Batch).filter(Batch.status == BatchStatus.FAILED).count()

    return {
        "total_accounts": total_accounts,
        "todays_uploads": todays_uploads,
        "pending_batches": pending_batches,
        "failed_batches": failed_batches,
    }
