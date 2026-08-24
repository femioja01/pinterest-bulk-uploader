"""Schedule Generator route — generate evenly-spaced publish dates and times for Pinterest bulk CSVs."""

from datetime import datetime, date, time, timedelta
from fastapi import APIRouter, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/generator", response_class=HTMLResponse)
async def generator_page(request: Request):
    """Render the Schedule Generator page."""
    # Pre-fill tomorrow's date
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request,
        "generator.html",
        {
            "active_page": "generator",
            "default_start_date": tomorrow,
        },
    )


@router.post("/api/generator/generate", response_class=JSONResponse)
async def generate_dates_api(data: dict = Body(...)):
    """API endpoint to generate dates based on input parameters."""
    start_date_str = data.get("start_date", "").strip()
    qty_per_day = int(data.get("qty_per_day", 25))
    total_qty = int(data.get("total_qty", 175))
    daily_start = data.get("daily_start", "08:00").strip()
    daily_end = data.get("daily_end", "22:00").strip()
    date_format = data.get("format", "iso")  # "iso", "standard", "date_only"

    if not start_date_str:
        return JSONResponse({"error": "Start date is required"}, status_code=400)
    if qty_per_day <= 0:
        return JSONResponse({"error": "Quantity per day must be at least 1"}, status_code=400)
    if total_qty <= 0:
        return JSONResponse({"error": "Total quantity must be at least 1"}, status_code=400)

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except Exception:
        return JSONResponse({"error": "Invalid start date format. Use YYYY-MM-DD"}, status_code=400)

    try:
        s_h, s_m = map(int, daily_start.split(":"))
        e_h, e_m = map(int, daily_end.split(":"))
    except Exception:
        s_h, s_m = 8, 0
        e_h, e_m = 22, 0

    start_seconds = s_h * 3600 + s_m * 60
    end_seconds = e_h * 3600 + e_m * 60
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 14 * 3600  # 14 hour default spread

    window_duration = end_seconds - start_seconds

    generated_dates = []
    current_day = start_date
    pins_generated = 0

    while pins_generated < total_qty:
        # Number of pins for today
        today_pins = min(qty_per_day, total_qty - pins_generated)
        
        if today_pins == 1:
            step_seconds = 0
        else:
            step_seconds = window_duration / (today_pins - 1) if today_pins > 1 else 0

        for i in range(today_pins):
            sec_offset = int(start_seconds + i * step_seconds)
            h = (sec_offset // 3600) % 24
            m = (sec_offset % 3600) // 60
            s = sec_offset % 60
            dt = datetime.combine(current_day, time(hour=h, minute=m, second=s))

            if date_format == "iso":
                formatted_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
            elif date_format == "standard":
                formatted_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            elif date_format == "date_only":
                formatted_str = dt.strftime("%Y-%m-%d")
            else:
                formatted_str = dt.strftime("%Y-%m-%dT%H:%M:%S")

            generated_dates.append(formatted_str)
            pins_generated += 1

        current_day += timedelta(days=1)

    days_span = (current_day - start_date).days

    return {
        "success": True,
        "dates": generated_dates,
        "count": len(generated_dates),
        "days_span": days_span,
        "first_date": generated_dates[0] if generated_dates else "",
        "last_date": generated_dates[-1] if generated_dates else "",
    }
