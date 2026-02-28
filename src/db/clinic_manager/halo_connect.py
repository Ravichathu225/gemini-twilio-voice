import base64
import json
import logging
import random
import datetime
import asyncio
import httpx
from typing import Any, Optional, List, Dict

from loguru import logger
from core import config as _cfg

class _Settings:
    HALO_SITE_ID         = _cfg.HALO_SITE_ID
    HALO_BASE_URL        = _cfg.HALO_BASE_URL
    HALO_SUBSCRIPTION_KEY = _cfg.HALO_SUBSCRIPTION_KEY

settings = _Settings()

from db.clinic_manager.queries import (
    GET_PATIENT_BY_PHONE,
    GET_PATIENT_ID_SIMPLE_MATCH,
    GET_PATIENT_ID_SIMPLE_MATCH_JADE,
    SEARCH_PATIENTS_BY_NAME,
)


class HaloConnectOperations:
    _instances = {}

    site_id = settings.HALO_SITE_ID

    def __new__(cls, site_id: str, *args, **kwargs):
        if site_id not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[site_id] = instance
            instance._initialize(site_id)
        return cls._instances[site_id]

    def _initialize(self, site_id: str):
        self.site_id = site_id
        logger.info(f"[{site_id}] Initializing Halo Connect async HTTP client...")
        # Integrator immediate queries endpoint
        base = settings.HALO_BASE_URL.rstrip("/")
        self.base_url = f"{base}/integrator/sites/{self.site_id}/queries/immediate"
        self.client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Ocp-Apim-Subscription-Key": f"{settings.HALO_SUBSCRIPTION_KEY}",
            },
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        # Cap concurrent in-flight requests to keep Halo happy (avoid 429s)
        self.sem = asyncio.Semaphore(8)
        logger.info(f"[{site_id}] Halo Connect async client initialized successfully.")

    async def aclose(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    @staticmethod
    def _extract_rows(body: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Base64-decode result.data and JSON-parse into a list of dicts."""
        data_b64 = body["result"]["data"]
        raw = base64.b64decode(data_b64)
        return json.loads(raw)

    @staticmethod
    def _is_away_that_date(text: str) -> bool:
        txt = (text or "").lower()
        return ("away" in txt and "that date" in txt)

    @staticmethod
    def _format_date_value(value: Any) -> str:
        if isinstance(value, datetime.datetime):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, datetime.date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, str):
            try:
                dt = datetime.datetime.fromisoformat(value)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                if len(value) >= 10:
                    return value[:10]
        return ""

    async def _call(self, name: str, params: List[Dict], execution_mode: str, command_type: str) -> Optional[Dict]:
        payload = {
            "command": {
                "text": name,
                "parameters": params,
                "executionMode": execution_mode,
                "type": command_type
            }
        }

        max_retries = 3
        base_backoff = 1.0  # seconds

        resp: Optional[httpx.Response] = None
        for attempt in range(1, max_retries + 1):
            try:
                async with self.sem:
                    resp = await self.client.post(self.base_url, json=payload)

                # Some app-level errors come back 200 with errorMessage
                data = {}
                try:
                    data = resp.json()
                    em_top = (data.get("errorMessage") or "")
                    em_nested = ((data.get("errorDetails") or {}).get("errorMessage") or "")
                    if self._is_away_that_date(em_top) or self._is_away_that_date(em_nested):
                        logger.info("Detected 'away that date' application error; returning None.")
                        return None
                except Exception:
                    pass

                resp.raise_for_status()
                return data

            except httpx.HTTPStatusError as exc:
                body_text = ""
                if exc.response is not None:
                    try:
                        body_text = exc.response.text or ""
                    except Exception:
                        body_text = ""

                if self._is_away_that_date(body_text):
                    return None

                # Respect Retry-After if present for 429/503
                delay = None
                if exc.response is not None and exc.response.status_code in (429, 503):
                    ra = exc.response.headers.get("Retry-After")
                    if ra:
                        try:
                            delay = float(ra)
                        except ValueError:
                            delay = None
                if delay is None:
                    delay = base_backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25)

                logger.warning(
                    "Attempt %d/%d failed for command '%s': %s; sleeping %.2fs",
                    attempt, max_retries, name, exc, delay
                )

                if attempt == max_retries:
                    logger.error("All %d attempts failed for command '%s'. Raising error.", max_retries, name)
                    logger.info(f"payload is: {payload!r}")
                    logger.info("Response body was: %r", body_text)
                    raise

                await asyncio.sleep(delay)

            except httpx.HTTPError as exc:
                delay = base_backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                logger.warning(
                    "Attempt %d/%d network error for command '%s': %s; sleeping %.2fs",
                    attempt, max_retries, name, exc, delay
                )

                if attempt == max_retries:
                    logger.error("All %d attempts failed for command '%s'. Raising error.", max_retries, name)
                    logger.info(f"payload is: {payload!r}")
                    raise

                await asyncio.sleep(delay)

    # ─────────────────────────── Public APIs ───────────────────────────

    async def get_patient_id(self, firstname: str, surname: str, dob: str, phone_number: str):
        try:
            sp_name = GET_PATIENT_ID_SIMPLE_MATCH
            params = [
                {"name": "@firstname", "value": f"%{firstname[:3]}%", "direction": "input", "type": "VarChar"},
                {"name": "@surname", "value": f"%{surname[:3]}%", "direction": "input", "type": "VarChar"},
                {"name": "@dob", "value": dob, "direction": "input", "type": "Date"},
                {"name": "@phone", "value": phone_number, "direction": "input", "type": "VarChar"}
            ]
            body = await self._call(sp_name, params, execution_mode="reader", command_type="text")
            rows = self._extract_rows(body) if body else []
            if rows:
                return (
                    rows[0].get("InternalID"),
                    rows[0].get("MedicareLineNo"),
                    rows[0].get("MedicareExpiry"),
                    rows[0].get("PensionCode"),
                    rows[0].get("DVACode"),
                )
            return None, None, None, None, None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_patient_id: {e}")
            return None, None, None, None, None

    async def get_patient_id_jade(self, firstname: str, surname: str, dob: str):
        try:
            sp_name = GET_PATIENT_ID_SIMPLE_MATCH_JADE
            params = [
                {"name": "@firstname", "value": f"%{firstname[:3]}%", "direction": "input", "type": "VarChar"},
                {"name": "@surname", "value": f"%{surname[:3]}%", "direction": "input", "type": "VarChar"},
                {"name": "@dob", "value": dob, "direction": "input", "type": "Date"}
            ]
            body = await self._call(sp_name, params, execution_mode="reader", command_type="text")
            rows = self._extract_rows(body) if body else []
            if rows:
                return (
                    rows[0].get("InternalID"),
                    rows[0].get("MedicareLineNo"),
                    rows[0].get("MedicareExpiry"),
                    rows[0].get("PensionCode"),
                    rows[0].get("DVACode"),
                )
            return None, None, None, None, None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_patient_id_jade: {e}")
            return None, None, None, None, None

    async def get_patient_by_phone(self, phone: str) -> Optional[List[Dict]]:
        try:
            sql_text = GET_PATIENT_BY_PHONE.strip()
            like_pattern = f"%{phone}%"
            params = [{"name": "@phone", "value": like_pattern, "direction": "input", "type": "VarChar"}]
            body = await self._call(sql_text, params, execution_mode="reader", command_type="text")
            rows = self._extract_rows(body) if body else []
            if not rows:
                return None

            patients = []
            for row in rows:
                patients.append({
                    "firstname": (row.get("FIRSTNAME") or "").strip(),
                    "lastname":  (row.get("SURNAME") or "").strip(),
                    "date_of_birth": self._format_date_value(row.get("DOB"))
                })
            return patients
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_patient_by_phone: {e}")
            return None

    async def search_patients_by_name(
        self,
        firstname: str,
        surname: str,
        dob: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        try:
            sql_text = SEARCH_PATIENTS_BY_NAME.strip()
            fname = (firstname or "").strip()
            lname = (surname or "").strip()
            params = [
                {
                    "name": "@firstname",
                    "value": f"%{fname[:3]}%" if fname else "%",
                    "direction": "input",
                    "type": "VarChar",
                },
                {
                    "name": "@surname",
                    "value": f"%{lname[:3]}%" if lname else "%",
                    "direction": "input",
                    "type": "VarChar",
                },
                {
                    "name": "@dob",
                    "value": dob,
                    "direction": "input",
                    "type": "Date",
                },
            ]
            body = await self._call(sql_text, params, execution_mode="reader", command_type="text")
            rows = self._extract_rows(body) if body else []
            if not rows:
                return None

            patients: List[Dict[str, Any]] = []
            for row in rows:
                patients.append({
                    "patient_id": row.get("InternalID"),
                    "firstname": (row.get("FIRSTNAME") or "").strip(),
                    "lastname": (row.get("SURNAME") or "").strip(),
                    "date_of_birth": self._format_date_value(row.get("DOB")),
                    "phone_number": (row.get("MOBILEPHONE") or "").strip(),
                })
            return patients or None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in search_patients_by_name: {e}")
            return None

    async def get_available_doctors(self) -> Optional[List[Dict]]:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_GetAppointmentBookUsers"
            body = await self._call(sp_name, [], execution_mode="reader", command_type="storedProcedure")
            rows = self._extract_rows(body) if body else []
            return rows if rows else None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_available_doctors: {e}")
            return None

    async def add_new_patient(self, firstname: str, lastname: str, dob: str, phone: str) -> bool:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_AddPatient"
            params = [
                {"name": "@titlecode",     "value": "0",         "direction": "input", "type": "Int"},
                {"name": "@firstname",     "value": firstname,   "direction": "input", "type": "VarChar"},
                {"name": "@middlename",    "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@surname",       "value": lastname,    "direction": "input", "type": "VarChar"},
                {"name": "@preferredname", "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@address1",      "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@address2",      "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@city",          "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@postcode",      "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@postaladdress", "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@postalcity",    "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@postalpostcode","value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@dob",           "value": dob,         "direction": "input", "type": "Date"},
                {"name": "@sexcode",       "value": "0",         "direction": "input", "type": "Int"},
                {"name": "@homephone",     "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@workphone",     "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@mobilephone",   "value": phone,       "direction": "input", "type": "VarChar"},
                {"name": "@medicareno",    "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@medicarelineno","value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@medicareexpiry","value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@pensioncode",   "value": "0",         "direction": "input", "type": "Int"},
                {"name": "@pensionno",     "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@pensionexpiry", "value": None,        "direction": "input", "type": "Date"},
                {"name": "@dvacode",       "value": "0",         "direction": "input", "type": "Int"},
                {"name": "@dvano",         "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@recordno",      "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@externalid",    "value": "",          "direction": "input", "type": "VarChar"},
                {"name": "@email",         "value": "",          "direction": "input", "type": "VarChar"}
            ]
            _ = await self._call(sp_name, params, execution_mode="nonQuery", command_type="storedProcedure")
            return True
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in add_new_patient: {e}")
            return False

    async def get_doctor_available_times(self, doctor_id: int, date: str) -> Optional[List[Dict]]:
        try:
            sp_name = "BP_GetFreeAppointments"
            params = [
                {"name": "@userid",  "value": str(doctor_id), "direction": "input", "type": "Int"},
                {"name": "@aptdate", "value": date,           "direction": "input", "type": "Date"}
            ]
            body = await self._call(sp_name, params, execution_mode="reader", command_type="storedProcedure")
            if body is None:
                return None
            rows = self._extract_rows(body)
            return rows if rows else None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_doctor_available_times: {e}")
            return None

    async def add_new_appointment(self, date: str, time: str, length: int, doctor_id: int, patient_id: int, location_id: int) -> Optional[int]:
        try:
            sp_name = ("DECLARE @AppointmentId INT; "
                       "EXEC @AppointmentId = BPSPATIENTS.dbo.BP_AddAppointment "
                       "@aptdate = @aptdate, @apttime = @apttime, @aptlen = @aptlen, "
                       "@practitionerid = @practitionerid, @patientid = @patientid, @locationid = @locationid; "
                       "SELECT @AppointmentId AS AppointmentId;")
            params = [
                {"name": "@aptdate",       "value": date,            "direction": "input", "type": "Date"},
                {"name": "@apttime",       "value": str(time),       "direction": "input", "type": "Int"},
                {"name": "@aptlen",        "value": str(length),     "direction": "input", "type": "Int"},
                {"name": "@practitionerid","value": str(doctor_id),  "direction": "input", "type": "Int"},
                {"name": "@patientid",     "value": str(patient_id), "direction": "input", "type": "Int"},
                {"name": "@locationid",    "value": str(location_id),"direction": "input", "type": "Int"}
            ]
            body = await self._call(sp_name, params, execution_mode="reader", command_type="text")
            rows = self._extract_rows(body) if body else []
            if rows:
                return rows[0].get("appointment_id") or rows[0].get("AppointmentId")
            return None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in add_new_appointment: {e}")
            return None

    async def update_appointment(self, appointment_id: int, appttype: int, length: int, reason: str):
        try:
            sp_name = "BPSPATIENTS.dbo.BP_UpdateAppointment"
            params = [
                {"name": "@aptid", "value": str(appointment_id), "direction": "input", "type": "Int"},
                {"name": "@apttype", "value": str(appttype),     "direction": "input", "type": "Int"},
                {"name": "@aptlen", "value": str(length),        "direction": "input", "type": "Int"},
                {"name": "@reason", "value": reason,             "direction": "input", "type": "VarChar"},
            ]
            _ = await self._call(sp_name, params, execution_mode="nonQuery", command_type="storedProcedure")
            return True
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in update_appointment: {e}")
            return False

    async def get_patient_appointments(self, patient_id: int) -> Optional[List[Dict]]:
        try:
            sp_name = "BP_GetPatientAppointments"
            params = [{"name": "@patientid", "value": str(patient_id), "direction": "input", "type": "Int"}]
            body = await self._call(sp_name, params, execution_mode="reader", command_type="storedProcedure")
            rows = self._extract_rows(body) if body else []
            return rows if rows else None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_patient_appointments: {e}")
            return None

    async def cancel_appointment(self, appointment_id: int) -> bool:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_CancelAppointment"
            params = [{"name": "@aptid", "value": str(appointment_id), "direction": "input", "type": "Int"}]
            _ = await self._call(sp_name, params, execution_mode="nonQuery", command_type="storedProcedure")
            return True
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in cancel_appointment: {e}")
            return False

    async def move_appointment(self, appointment_id: int, date: str, time: int, userid: int, locationid: int) -> bool:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_MoveAppointment"
            params = [
                {"name": "@aptid", "value": str(appointment_id), "direction": "input", "type": "Int"},
                {"name": "@aptdate", "value": date,              "direction": "input", "type": "Date"},
                {"name": "@apttime", "value": str(time),         "direction": "input", "type": "Int"},
                {"name": "@practitionerid", "value": str(userid),"direction": "input", "type": "Int"},
                {"name": "@locationid", "value": str(locationid),"direction": "input", "type": "Int"},
            ]
            _ = await self._call(sp_name, params, execution_mode="nonQuery", command_type="storedProcedure")
            return True
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in move_appointment: {e}")
            return False

    async def get_patient_reports(self, patient_id: int) -> Optional[List[Dict]]:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_GetInvestigationSummary"
            params = [{"name": "@InternalID", "value": str(patient_id), "direction": "input", "type": "Int"}]
            body = await self._call(sp_name, params, execution_mode="reader", command_type="storedProcedure")
            rows = self._extract_rows(body) if body else []
            return rows if rows else None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_patient_reports: {e}")
            return None

    async def send_message(self, user_id: int, subject: str, message: str) -> bool:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_AddMessage"
            params = [
                {"name": "@userid", "value": str(user_id), "direction": "input", "type": "Int"},
                {"name": "@subject", "value": str(subject), "direction": "input", "type": "VarChar"},
                {"name": "@message", "value": str(message), "direction": "input", "type": "VarChar"},
            ]
            _ = await self._call(sp_name, params, execution_mode="nonQuery", command_type="storedProcedure")
            return True
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in send_message: {e}")
            return False

    async def get_all_users(self) -> Optional[List[Dict]]:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_GetAllUsers"
            body = await self._call(sp_name, [], execution_mode="reader", command_type="storedProcedure")
            rows = self._extract_rows(body) if body else []
            return rows if rows else None
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in get_all_users: {e}")
            return None

    async def arrive_patient(self, appointment_id: int) -> bool:
        try:
            sp_name = "BPSPATIENTS.dbo.BP_ArriveAppointment"
            params = [{"name": "@aptid", "value": str(appointment_id), "direction": "input", "type": "Int"}]
            res = await self._call(sp_name, params, execution_mode="nonQuery", command_type="storedProcedure")
            return res
        except Exception as e:
            logger.error(f"[{self.site_id}] Exception in arrive_patient: {e}")
            return False
