"""
functions/db_functions.py — Gemini Live function call declarations + handlers.

Gemini Live sends a toolCall message with functionCalls.
We dispatch to the matching handler, call the Halo DB, and return a plain dict.
The caller (media_stream.py) sends the result back as a toolResponse.

No pipecat dependency — pure asyncio + httpx.
"""

import logging
from typing import Any, Dict

from db.clinic_manager.halo_connect import HaloConnectOperations
from core.config import HALO_SITE_ID

log = logging.getLogger(__name__)


def _get_halo() -> HaloConnectOperations:
    return HaloConnectOperations(HALO_SITE_ID)


# ═══════════════════════════════════════════════════════════════
# 1. TOOL DECLARATIONS  (sent to Gemini in setup)
# ═══════════════════════════════════════════════════════════════

TOOL_DECLARATIONS = [
    {
        "name": "register_new_patient",
        "description": (
            "Register a brand new patient in the clinic system. "
            "Call this ONLY when the caller confirms they are a NEW patient who has never visited before. "
            "Collect first name, last name, date of birth, and mobile phone number before calling."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "firstname": {"type": "string", "description": "Patient's first name."},
                "lastname":  {"type": "string", "description": "Patient's last name / surname."},
                "dob":       {"type": "string", "description": "Date of birth in YYYY-MM-DD format."},
                "phone":     {"type": "string", "description": "Mobile phone number."},
            },
            "required": ["firstname", "lastname", "dob", "phone"],
        },
    },
    {
        "name": "find_patient",
        "description": (
            "Find a patient in the clinic system by first name, last name, and date of birth. "
            "Optionally pass a phone number for a more accurate match. "
            "Call this when the caller wants to book, reschedule, or cancel an appointment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_firstname": {"type": "string", "description": "Patient's first name."},
                "patient_lastname":  {"type": "string", "description": "Patient's last name / surname."},
                "patient_dob":       {"type": "string", "description": "Date of birth in YYYY-MM-DD format."},
                "patient_phone":     {"type": "string", "description": "Mobile phone number (optional)."},
            },
            "required": ["patient_firstname", "patient_lastname", "patient_dob"],
        },
    },
    {
        "name": "search_patients",
        "description": (
            "Search for patients by name and optionally date of birth. "
            "Returns up to 40 matching records. Use when exact details are uncertain."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "firstname": {"type": "string", "description": "First name (partial match)."},
                "surname":   {"type": "string", "description": "Last name (partial match)."},
                "dob":       {"type": "string", "description": "Date of birth YYYY-MM-DD (optional)."},
            },
            "required": ["firstname", "surname"],
        },
    },
    {
        "name": "get_available_doctors",
        "description": "Retrieve the list of doctors available for booking.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_doctor_available_times",
        "description": "Get free appointment slots for a specific doctor on a given date.",
        "parameters": {
            "type": "object",
            "properties": {
                "doctor_id": {"type": "integer", "description": "Doctor's unique ID."},
                "date":      {"type": "string",  "description": "Date to check in YYYY-MM-DD format."},
            },
            "required": ["doctor_id", "date"],
        },
    },
    {
        "name": "book_appointment",
        "description": (
            "Book a new appointment for a patient. "
            "Requires patient_id, doctor_id, date, time (minutes from midnight), and duration."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id":         {"type": "integer", "description": "Internal patient ID."},
                "doctor_id":          {"type": "integer", "description": "Doctor / practitioner ID."},
                "appointment_date":   {"type": "string",  "description": "Date in YYYY-MM-DD format."},
                "appointment_time":   {"type": "integer", "description": "Start time as minutes from midnight (e.g. 540 = 09:00)."},
                "appointment_length": {"type": "integer", "description": "Duration in minutes (e.g. 15)."},
                "location_id":        {"type": "integer", "description": "Clinic location ID (default 1)."},
            },
            "required": ["patient_id", "doctor_id", "appointment_date", "appointment_time", "appointment_length"],
        },
    },
    {
        "name": "get_patient_appointments",
        "description": "Retrieve all upcoming appointments for a patient.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "integer", "description": "Internal patient ID."},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "reschedule_appointment",
        "description": "Reschedule an existing appointment to a new date and/or time.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer", "description": "Appointment ID to reschedule."},
                "new_date":       {"type": "string",  "description": "New date in YYYY-MM-DD format."},
                "new_time":       {"type": "integer", "description": "New start time as minutes from midnight."},
                "doctor_id":      {"type": "integer", "description": "Doctor ID for rescheduled appointment."},
                "location_id":    {"type": "integer", "description": "Clinic location ID (default 1)."},
            },
            "required": ["appointment_id", "new_date", "new_time", "doctor_id"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel an existing appointment.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer", "description": "Appointment ID to cancel."},
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "get_patient_test_results",
        "description": "Look up medical test / lab results for a patient.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "integer", "description": "Internal patient ID."},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "transfer_call",
        "description": (
            "Transfer the call to a human receptionist. "
            "Use when caller asks to speak to a human, agent, or real person."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Brief reason for transfer."},
            },
            "required": ["reason"],
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# 2. FUNCTION HANDLERS  (called by dispatch_function_call)
# ═══════════════════════════════════════════════════════════════

async def _handle_register_new_patient(args: Dict) -> Dict:
    halo = _get_halo()
    fn    = args.get("firstname", "")
    ln    = args.get("lastname", "")
    dob   = args.get("dob", "")
    phone = args.get("phone", "")
    log.info(f"[fn] register_new_patient {fn} {ln} dob={dob} phone={phone}")
    try:
        ok = await halo.add_new_patient(fn, ln, dob, phone)
        if ok:
            # After creating, look up the new patient to return their ID
            pid, ml, me, pc, dva = await halo.get_patient_id(fn, ln, dob, phone)
            if pid:
                return {
                    "status": "created",
                    "patient_id": pid,
                    "message": f"New patient {fn} {ln} registered successfully. Patient ID: {pid}.",
                }
            # Created but ID not returned yet — still success
            return {"status": "created", "message": f"New patient {fn} {ln} registered successfully."}
        return {"status": "failed", "message": "Could not register patient. Please try again."}
    except Exception as e:
        log.error(f"[fn] register_new_patient error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_find_patient(args: Dict) -> Dict:
    halo = _get_halo()
    fn = args.get("patient_firstname", "")
    ln = args.get("patient_lastname", "")
    dob = args.get("patient_dob", "")
    phone = args.get("patient_phone", "")
    log.info(f"[fn] find_patient {fn} {ln} dob={dob}")
    try:
        if phone:
            pid, ml, me, pc, dva = await halo.get_patient_id(fn, ln, dob, phone)
        else:
            pid, ml, me, pc, dva = await halo.get_patient_id_jade(fn, ln, dob)
        if pid:
            return {"status": "found", "patient_id": pid, "medicare_line_no": ml,
                    "medicare_expiry": me, "pension_code": pc, "dva_code": dva}
        return {"status": "not_found", "message": f"No patient found for {fn} {ln} DOB {dob}."}
    except Exception as e:
        log.error(f"[fn] find_patient error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_search_patients(args: Dict) -> Dict:
    halo = _get_halo()
    log.info(f"[fn] search_patients {args}")
    try:
        patients = await halo.search_patients_by_name(
            args.get("firstname", ""), args.get("surname", ""), args.get("dob")
        )
        if patients:
            return {"status": "found", "count": len(patients), "patients": patients}
        return {"status": "not_found", "message": "No matching patients found."}
    except Exception as e:
        log.error(f"[fn] search_patients error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_get_available_doctors(args: Dict) -> Dict:
    halo = _get_halo()
    log.info("[fn] get_available_doctors")
    try:
        doctors = await halo.get_available_doctors()
        if doctors:
            return {"status": "found", "count": len(doctors), "doctors": doctors}
        return {"status": "not_found", "message": "No doctors available."}
    except Exception as e:
        log.error(f"[fn] get_available_doctors error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_get_doctor_available_times(args: Dict) -> Dict:
    halo = _get_halo()
    doctor_id = args.get("doctor_id")
    date = args.get("date", "")
    log.info(f"[fn] get_doctor_available_times doctor={doctor_id} date={date}")
    try:
        slots = await halo.get_doctor_available_times(doctor_id, date)
        if slots:
            return {"status": "found", "count": len(slots), "available_slots": slots}
        return {"status": "not_found", "message": f"No slots for doctor {doctor_id} on {date}."}
    except Exception as e:
        log.error(f"[fn] get_doctor_available_times error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_book_appointment(args: Dict) -> Dict:
    halo = _get_halo()
    log.info(f"[fn] book_appointment {args}")
    try:
        appt_id = await halo.add_new_appointment(
            date=args.get("appointment_date", ""),
            time=str(args.get("appointment_time")),
            length=args.get("appointment_length", 15),
            doctor_id=args.get("doctor_id"),
            patient_id=args.get("patient_id"),
            location_id=args.get("location_id", 1),
        )
        if appt_id:
            return {"status": "success", "appointment_id": appt_id,
                    "message": f"Appointment booked. ID: {appt_id}"}
        return {"status": "failed", "message": "Slot may no longer be available."}
    except Exception as e:
        log.error(f"[fn] book_appointment error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_get_patient_appointments(args: Dict) -> Dict:
    halo = _get_halo()
    pid = args.get("patient_id")
    log.info(f"[fn] get_patient_appointments patient={pid}")
    try:
        appts = await halo.get_patient_appointments(pid)
        if appts:
            return {"status": "found", "count": len(appts), "appointments": appts}
        return {"status": "not_found", "message": "No appointments found."}
    except Exception as e:
        log.error(f"[fn] get_patient_appointments error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_reschedule_appointment(args: Dict) -> Dict:
    halo = _get_halo()
    log.info(f"[fn] reschedule_appointment {args}")
    try:
        ok = await halo.move_appointment(
            appointment_id=args.get("appointment_id"),
            date=args.get("new_date", ""),
            time=args.get("new_time"),
            userid=args.get("doctor_id"),
            locationid=args.get("location_id", 1),
        )
        if ok:
            return {"status": "success", "message": "Appointment rescheduled."}
        return {"status": "failed", "message": "Could not reschedule. Try a different time."}
    except Exception as e:
        log.error(f"[fn] reschedule_appointment error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_cancel_appointment(args: Dict) -> Dict:
    halo = _get_halo()
    appt_id = args.get("appointment_id")
    log.info(f"[fn] cancel_appointment {appt_id}")
    try:
        ok = await halo.cancel_appointment(appt_id)
        if ok:
            return {"status": "success", "message": "Appointment cancelled."}
        return {"status": "failed", "message": "Could not cancel appointment."}
    except Exception as e:
        log.error(f"[fn] cancel_appointment error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_get_patient_test_results(args: Dict) -> Dict:
    halo = _get_halo()
    pid = args.get("patient_id")
    log.info(f"[fn] get_patient_test_results patient={pid}")
    try:
        results = await halo.get_patient_reports(pid)
        if results:
            return {"status": "found", "count": len(results), "results": results}
        return {"status": "not_found", "message": "No test results found."}
    except Exception as e:
        log.error(f"[fn] get_patient_test_results error: {e}")
        return {"status": "error", "message": str(e)}


async def _handle_transfer_call(args: Dict, call_sid: str = "") -> Dict:
    """Transfer via Twilio REST API."""
    import httpx
    from core.config import (
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
        TWILIO_PHONE_NUMBER, TRANSFER_PHONE_NUMBER,
    )
    reason = args.get("reason", "Caller requested transfer")
    log.info(f"[fn] transfer_call reason={reason} call_sid={call_sid}")
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TRANSFER_PHONE_NUMBER, call_sid]):
        return {"status": "error", "message": "Transfer not configured or no active call."}
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Say>Please hold while I connect you to a receptionist.</Say>'
        f'<Dial callerId="{TWILIO_PHONE_NUMBER}" timeout="30">'
        f'<Number>{TRANSFER_PHONE_NUMBER}</Number></Dial>'
        '<Say>Sorry, we could not connect you. Please try again later.</Say>'
        '</Response>'
    )
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{call_sid}.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"Twiml": twiml},
            )
            resp.raise_for_status()
        return {"status": "transferring", "to": TRANSFER_PHONE_NUMBER}
    except Exception as e:
        log.error(f"[fn] transfer_call error: {e}")
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════
# 3. DISPATCHER  (called from media_stream.py)
# ═══════════════════════════════════════════════════════════════

_HANDLERS = {
    "register_new_patient":        _handle_register_new_patient,
    "find_patient":                _handle_find_patient,
    "search_patients":             _handle_search_patients,
    "get_available_doctors":       _handle_get_available_doctors,
    "get_doctor_available_times":  _handle_get_doctor_available_times,
    "book_appointment":            _handle_book_appointment,
    "get_patient_appointments":    _handle_get_patient_appointments,
    "reschedule_appointment":      _handle_reschedule_appointment,
    "cancel_appointment":          _handle_cancel_appointment,
    "get_patient_test_results":    _handle_get_patient_test_results,
    "transfer_call":               _handle_transfer_call,
}


async def dispatch_function_call(name: str, args: Dict, call_sid: str = "") -> Dict:
    """
    Route a Gemini toolCall to the correct handler.
    Returns a plain dict — the caller wraps it in a toolResponse message.
    """
    handler = _HANDLERS.get(name)
    if not handler:
        log.warning(f"[fn] Unknown function: {name}")
        return {"status": "error", "message": f"Unknown function: {name}"}
    if name == "transfer_call":
        return await handler(args, call_sid=call_sid)
    return await handler(args)
