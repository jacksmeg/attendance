# NIA IVSP Onboarding Pack

Last checked: 2026-05-15

## Official Position

The official Ghana Card verification route is the National Identification Authority (NIA)
Identity Verification System Platform (IVSP).

NIA says institutions should onboard to IVSP for real-time Ghana Card verification and
should contact `idverification@nia.gov.gh` to begin. Access is granted only after:

1. the institution submits the request form,
2. NIA reviews the documents,
3. technical setup is completed, and
4. a contract is executed between NIA and the institution.

Official sources:

- https://nia.gov.gh/service/verification-services/
- https://nia.gov.gh/institutions-urged-to-use-nia-ivsp-for-authentic-ghana-card-verification/
- https://nia.gov.gh/forms/
- https://nia.gov.gh/wp-content/uploads/IVSP-User-Request-Form_v2.225F.pdf

## What This System Already Has Ready

The attendance system already stores the core fields that fit the IVSP onboarding dataset:

- surname / first name
- date of birth
- sex
- nationality
- place of birth
- residential address
- digital address
- phone
- department / occupation-style internal role
- fingerprint-based internal identity check

Saved in staff records now:

- `ghana_card_number`
- `nationality`
- `sex`
- `date_of_birth`
- `place_of_birth`
- `residential_address`
- `digital_address`
- `ghana_card_verified_at`
- `ghana_card_verified_by`

## NIA Checklist

Prepare these before contacting NIA:

1. Business Registration Certificate
2. Business License from your regulator
3. Legal basis permitting collection of personal data
4. Valid Data Protection Commission certificate
5. SSNIT certificate
6. Organization profile
7. Process flow showing how Ghana Card verification is used in operations
8. Dataset request summary

## Suggested Dataset Request

For this attendance system, start with the minimum practical dataset:

- Surname
- Forename(s)
- Date of Birth
- Sex
- Nationality
- Place of Birth
- Residential Address
- Digital Address
- Telephone Number(s)

Optional later:

- Email Address
- Occupation
- SSNIT ID Number
- Driver's License Number
- Passport Number
- NHIS Number
- Voter ID Number
- Tax Identification Number

## Suggested Process Flow Summary

Use this wording for the onboarding form/process note:

The institution uses the Ghana Card as part of staff identity verification and internal
compliance workflows. Staff records are first captured into the internal attendance and HR
platform. During verification, the staff member presents Ghana Card details and confirms
identity in person. The institution then verifies identity through the official NIA IVSP,
matches the verified person to the internal staff profile, and stores only the permitted
verification result and approved datasets required for operational compliance.

## Email Draft

Use the draft in:

- `docs/nia_ivsp_request_email.txt`

## Important Note

Do not use unofficial Ghana Card APIs or card-copying tools. NIA states IVSP is the only
approved and secure method for authentic real-time verification.

## After NIA Approves

Once NIA gives you:

- endpoint or connection details
- credentials / certificates / tokens
- technical specification
- contract / permitted dataset scope

the next integration step in this project is:

1. add IVSP credentials to secure config,
2. create an `nia_ivsp` service adapter,
3. connect the Ghana Card Verification page to live IVSP checks,
4. store only permitted fields and verification status,
5. add audit logs for every verification request.
