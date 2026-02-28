# queries.py

GET_PATIENT_ID = """
EXECUTE BPSPATIENTS.dbo.BP_GetPatientByNameDOB 
    @firstname = ?, 
    @surname = ?, 
    @dob = ?;
"""

GET_PATIENT_ID_SIMPLE_MATCH = """SELECT 
    InternalID, MedicareLineNo, MedicareExpiry, PensionCode, DVACode
FROM 
    BPSPATIENTS.dbo.PATIENTS 
WHERE 
    FIRSTNAME LIKE @firstname 
    AND SURNAME LIKE @surname 
    AND CAST(DOB AS DATE) = CAST(@dob AS DATE)
    AND RECORDSTATUS = 1 AND PATIENTSTATUS = 1
    AND REPLACE(MOBILEPHONE, ' ', '') LIKE @phone
"""

GET_PATIENT_ID_SIMPLE_MATCH_JADE = """SELECT 
    InternalID, MedicareLineNo, MedicareExpiry, PensionCode, DVACode
FROM 
    BPSPATIENTS.dbo.PATIENTS 
WHERE 
    FIRSTNAME LIKE @firstname 
    AND SURNAME LIKE @surname 
    AND CAST(DOB AS DATE) = CAST(@dob AS DATE)
    AND RECORDSTATUS = 1 AND PATIENTSTATUS = 1
"""

GET_PATIENT_BY_PHONE = """
SELECT 
    FIRSTNAME, SURNAME, DOB
FROM 
    BPSPATIENTS.dbo.PATIENTS 
WHERE 
    REPLACE(MOBILEPHONE, ' ', '') LIKE @phone AND RECORDSTATUS = 1 AND PATIENTSTATUS = 1
"""

SEARCH_PATIENTS_BY_NAME = """
SELECT TOP (40)
    InternalID,
    FIRSTNAME,
    SURNAME,
    DOB,
    MOBILEPHONE
FROM 
    BPSPATIENTS.dbo.PATIENTS
WHERE 
    RECORDSTATUS = 1 
    AND PATIENTSTATUS = 1
    AND FIRSTNAME LIKE @firstname
    AND SURNAME LIKE @surname
    AND (
        @dob IS NULL 
        OR CAST(DOB AS DATE) = CAST(@dob AS DATE)
    )
ORDER BY DOB DESC;
"""

GET_DOCTORS = """EXEC BPSPATIENTS.dbo.BP_GetAppointmentBookUsers;"""

ADD_PATIENT = """
EXEC BPSPATIENTS.dbo.BP_AddPatient
    @titlecode = 0,
    @firstname = ?, -- replace with actual first name
    @middlename = '', -- empty value
    @surname = ?, -- replace with actual surname
    @preferredname = '', -- empty value
    @address1 = '', -- empty value
    @address2 = '', -- empty value
    @city = '', -- empty value
    @postcode = '', -- empty value
    @postaladdress = '', -- empty value
    @postalcity = '', -- empty value
    @postalpostcode = '', -- empty value
    @dob = ?, -- replace with actual dob (yyyy-mm-dd format)
    @sexcode = 0, -- default value, required
    @homephone = '', -- replace with actual phone number
    @workphone = '', -- empty value
    @mobilephone = ?, -- empty value
    @medicareno = '', -- empty value
    @medicarelineno = '', -- empty value
    @medicareexpiry = '', -- empty value
    @pensioncode = 0, -- default value, required
    @pensionno = '', -- empty value
    @pensionexpiry = null, -- null value
    @dvacode = 0, -- default value, required
    @dvano = '', -- empty value
    @recordno = '', -- empty value
    @externalid = '', -- empty value
    @email = ''; -- empty value, but required
"""

GET_AVAILABLE_TIMES = """
EXEC BPSPATIENTS.dbo.BP_GetFreeAppointments 
    @userid = ?,
    @aptdate = ?;
"""

ADD_APPOINTMENT = """
    DECLARE @return_value INT;
    EXEC @return_value = BPSPATIENTS.dbo.BP_AddAppointment 
        @aptdate=?, @apttime=?, @aptlen=?, 
        @practitionerid=?, @patientid=?, @locationid=?;
    SELECT appointment_id = @return_value;
"""

GET_PATIENT_APPOINTMENTS = """
EXECUTE BPSPATIENTS.dbo.BP_GetPatientAppointments 
    @patientid = ?;
"""

CANCEL_APPOINTMENT = """
EXECUTE BPSPATIENTS.dbo.BP_CancelAppointment
    @aptid = ?;
"""
