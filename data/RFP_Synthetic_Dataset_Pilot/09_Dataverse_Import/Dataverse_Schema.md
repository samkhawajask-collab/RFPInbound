# Dataverse Import Schema (publisher prefix hrs_)

Import in file-number order so lookups resolve. Dates are ISO 8601 (yyyy-mm-dd). Use the *id* column of each table as its alternate key.

| File | Table | Alternate key | Lookups | Choice columns |
|---|---|---|---|---|
| 01_hrs_category.csv | hrs_category | hrs_categorycode | hrs_primarysmeid, hrs_backupsmeid -> hrs_sme | - |
| 02_hrs_sme.csv | hrs_sme | hrs_smeid | - | - |
| 03_hrs_answer.csv | hrs_answer | hrs_answerid | hrs_categorycode -> hrs_category; hrs_supersededby -> hrs_answer (self) | hrs_answertype (Short, Long-form, Boilerplate); hrs_approvalstatus (Approved, Draft, Retired); hrs_confidentiality (Public, NDA-only); hrs_product |
| 04_hrs_policyrule.csv | hrs_policyrule | hrs_ruleid | hrs_escalationsmeid -> hrs_sme | hrs_severity (Block, Warn, Info); hrs_category |
| 05_hrs_approver.csv | hrs_approver | hrs_level | - | - |
| 06_hrs_opportunity.csv | hrs_opportunity | hrs_opportunityid | - | hrs_gonogo (Go, No-Go) |
| 07_hrs_certification.csv | hrs_certification | hrs_certid | - | hrs_statusasof20261027 (Valid, Expired) - recompute from hrs_expires at run time |
| 08_hrs_insurance.csv | hrs_insurance | hrs_coverage | - | - |
| 09_hrs_casestudy.csv | hrs_casestudy | hrs_casestudyid | - | - |
| 10_hrs_teammember.csv | hrs_teammember | hrs_staffid | - | - |
| 11_hrs_pastproposal.csv | hrs_pastproposal | hrs_proposalid | hrs_answersused is a semicolon-separated list of hrs_answerid values | hrs_outcome (Won, Lost, Withdrawn) |
| 90_eval_goldrequirement.csv | hrs_evalgoldrequirement (optional) | hrs_rfp + hrs_reqid | - | For an evaluation environment only; never expose to the production agent |

Column types: text unless noted; hrs_answertext, hrs_condition, hrs_approvedposition, hrs_evaluatorfeedback = multiline text (up to 4,000 chars); hrs_timesused, hrs_finalrank, hrs_yearsexperience, hrs_evaluationscore = whole number; hrs_winrate = decimal (0-1); all *date / hrs_lastreviewed / hrs_issued / hrs_expires = date only.