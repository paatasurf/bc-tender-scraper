# Parsed Identity Merge — Full Review List (Scenario B)

**Generated:** 2026-07-04T06:28:37.802525+00:00  
**Safe merge groups:** 167  
**Excluded multi-root groups:** 34  
**Dry-run artifact:** `exports/parsed_identity_merge_report.json`  
**Implementation commit:** `a2d79cb` (Scenario B code)  
**Apply verification:** `git_commit_sha` + `dataset_fingerprint` in artifact must match HEAD + live DB at apply time.

### Before `--apply`

Regenerate the artifact at the commit you intend to apply from (do not commit between report and apply):

```powershell
python scripts/run_parsed_identity_canonical_merge.py --report --review-md --use-production
python scripts/run_parsed_identity_canonical_merge.py --apply --allow-production
```

Review every safe group below before approving apply.

## 1. LQ Design GROUP Ltd

- **Norm key:** `lqdesign`
- **Distinct roots:** 24
- **Distinct companies:** 24
- **PI applicant rows:** 1954
- **Winning canonical company:** 292 — QI LI DBA: LQ Design GROUP Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 292 | 1954 | 1 | QI LI DBA: LQ Design GROUP Ltd |
| 548949 | 76 | 1 | construction |
| 525 | 32 | 1 | Anthony Sun DBA: Home Vitality Solutions Inc. |
| 604 | 28 | 1 | Baljeet Singh Cheema DBA: JHN Development Ltd. |
| 8559 | 10 | 1 | Alex Song DBA: Transvan Development Ltd. |
| 549092 | 4 | 1 | Aikid Design/Management Inc. |
| 6375 | 2 | 1 | Big Tree Construction Ltd |
| 572959 | 0 | 1 | Kylin Construction |
| 572960 | 0 | 1 | Enrich Custom Homes Ltd |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 572971 | 0 | 1 | East West Excavating 2022 Ltd |
| 573007 | 0 | 1 | Hantech Construction Ltd |
| 573010 | 0 | 1 | TX Contracting Ltd |
| 573032 | 0 | 1 | Cansim Construction Ltd |
| 573033 | 0 | 1 | Double Star Enterprises Ltd. |
| 573034 | 0 | 1 | REFINED HOMES PROJECTS LTD. |
| 573038 | 0 | 1 | 0992346 BC Ltd |
| 573055 | 0 | 1 | Van-City Excavating Ltd |
| 573056 | 0 | 1 | DEMOLITION 2008 LTD |
| 573057 | 0 | 1 | RENOVATION LTD. |
| 573062 | 0 | 1 | Contracting Ltd |
| 573076 | 0 | 1 | C-Val Ltd |
| 573099 | 0 | 1 | New legacy Homes Corp |
| 573111 | 0 | 1 | Canstone Development Ltd |

## 2. Architectural Collective Inc.

- **Norm key:** `architecturalcollective`
- **Distinct roots:** 8
- **Distinct companies:** 8
- **PI applicant rows:** 904
- **Winning canonical company:** 548732 — Architect

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548732 | 1072 | 1 | Architect |
| 3648 | 8 | 1 | Amritpal Kang DBA: Bigcity Excavation Ltd. |
| 548959 | 0 | 1 | Architectural Collective Inc. |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 573011 | 0 | 1 | East West Excavating Ltd |
| 573017 | 0 | 1 | K Excavation and Demolition Services Ltd |
| 573050 | 0 | 1 | Mahnger Homes Ltd |
| 573101 | 0 | 1 | K Excavation and Demolition Services Ltd - 604-617-8715 |

## 3. DWG Design Work Group Ltd.

- **Norm key:** `dwgdesignwork`
- **Distinct roots:** 8
- **Distinct companies:** 8
- **PI applicant rows:** 901
- **Winning canonical company:** 548940 — DWG Design Work Group Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548940 | 901 | 1 | DWG Design Work Group Ltd. |
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 572985 | 0 | 1 | Canadian Excavation Ltd |
| 573041 | 0 | 1 | Octiscapes Site Services Ltd |
| 573055 | 0 | 1 | Van-City Excavating Ltd |
| 573094 | 0 | 1 | Nihal Construction Ltd. |
| 573124 | 0 | 1 | East West Excavating LTD: 604-763-5301 |

## 4. MBD Maple Building Design Inc.

- **Norm key:** `mbdmaplebuildingdesign`
- **Distinct roots:** 8
- **Distinct companies:** 8
- **PI applicant rows:** 256
- **Winning canonical company:** 1976 — Asit Biswas DBA: MBD Maple Building Design Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1976 | 256 | 1 | Asit Biswas DBA: MBD Maple Building Design Inc. |
| 6553 | 2 | 1 | BALVIR SANDHU DBA: Van Coast Construction Ltd. |
| 573002 | 0 | 1 | Cedar Van Homes Inc |
| 573012 | 0 | 1 | Liwa Enterprises Ltd |
| 573015 | 0 | 1 | Bhullar Excavating and Demolition |
| 573040 | 0 | 1 | Astrawest Design Build Inc |
| 573070 | 0 | 1 | maintained at 20' Lane at all times in accordance with the Building By-law. NEW R1-1 BYLAW ***This permit is issued under the |
| 573104 | 0 | 1 | Residential Builder- Xiaoming Wang |

## 5. Vincent Wan Design

- **Norm key:** `vincentwandesign`
- **Distinct roots:** 8
- **Distinct companies:** 8
- **PI applicant rows:** 1571
- **Winning canonical company:** 5 — Vincent Wan DBA: Vincent Wan Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 5 | 1571 | 1 | Vincent Wan DBA: Vincent Wan Design |
| 3141 | 24 | 1 | Vantac Management Ltd |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 572966 | 0 | 1 | Wei Ga Enterprise Ltd |
| 572971 | 0 | 1 | East West Excavating 2022 Ltd |
| 572977 | 0 | 1 | Dhinsa Ventures Corp |
| 573056 | 0 | 1 | DEMOLITION 2008 LTD |
| 573073 | 0 | 1 | 1149692 BC Ltd. |

## 6. Lineform Architecture Inc

- **Norm key:** `lineformarchitecture`
- **Distinct roots:** 7
- **Distinct companies:** 7
- **PI applicant rows:** 282
- **Winning canonical company:** 8017 — Michael Lu DBA: Lineform Architecture Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8017 | 282 | 1 | Michael Lu DBA: Lineform Architecture Inc |
| 548799 | 82 | 1 | PHW HOMES INC. |
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 4153 | 26 | 1 | Gavin Mcleod DBA: Averra Developments Inc. |
| 3249 | 4 | 1 | Perdip Moore DBA: P.D. Moore Homes Inc. |
| 7479 | 3 | 1 | Vignarajah Sellathurai DBA: Vithu developments Ltd. |
| 572969 | 0 | 1 | Excavating Ltd. |

## 7. Raj Home Design

- **Norm key:** `rajhomedesign`
- **Distinct roots:** 6
- **Distinct companies:** 6
- **PI applicant rows:** 654
- **Winning canonical company:** 334 — Bhupinder (Raj) Singh DBA: Raj Home Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 334 | 654 | 1 | Bhupinder (Raj) Singh DBA: Raj Home Design |
| 572971 | 0 | 1 | East West Excavating 2022 Ltd |
| 573005 | 0 | 1 | Jet Custom Home Ltd |
| 573013 | 0 | 1 | AK Sidhu Builders Corporation |
| 573053 | 0 | 1 | Anmol Holding Ltd |
| 573063 | 0 | 1 | Metro Contracting Ltd |

## 8. Elite Premium Home Design Ltd.

- **Norm key:** `elitepremiumhomedesign`
- **Distinct roots:** 5
- **Distinct companies:** 5
- **PI applicant rows:** 146
- **Winning canonical company:** 866 — Lynn Lee / Elite Premium Home Design Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 866 | 146 | 1 | Lynn Lee / Elite Premium Home Design Ltd. |
| 7535 | 6 | 1 | Tao Yu DBA: MTS Innovation Ltd |
| 7866 | 6 | 1 | Dasheng Development LTD. DBA: Development |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 573057 | 0 | 1 | RENOVATION LTD. |

## 9. TChen Custom Homes / TC Studio

- **Norm key:** `tcstudio`
- **Distinct roots:** 5
- **Distinct companies:** 5
- **PI applicant rows:** 300
- **Winning canonical company:** 3290 — Terry Chen DBA: TChen Custom Homes / TC Studio

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3290 | 300 | 1 | Terry Chen DBA: TChen Custom Homes / TC Studio |
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 572969 | 0 | 1 | Excavating Ltd. |
| 572971 | 0 | 1 | East West Excavating 2022 Ltd |
| 572984 | 0 | 1 | MCCM Residential Ltd |

## 10. Wiedemann Architectural Design

- **Norm key:** `wiedemannarchitecturaldesign`
- **Distinct roots:** 5
- **Distinct companies:** 5
- **PI applicant rows:** 492
- **Winning canonical company:** 548966 — Wiedemann Architectural Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548966 | 492 | 1 | Wiedemann Architectural Design |
| 573006 | 0 | 1 | Vici Custom Builders Ltd |
| 573011 | 0 | 1 | East West Excavating Ltd |
| 573118 | 0 | 1 | MKL Homes Ltd |
| 573128 | 0 | 1 | Palas Homes Ltd |

## 11. Eric Stine Architect Inc.

- **Norm key:** `ericstinearchitect`
- **Distinct roots:** 4
- **Distinct companies:** 4
- **PI applicant rows:** 162
- **Winning canonical company:** 430 — Eric Stine DBA: Eric Stine Architect Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 430 | 162 | 1 | Eric Stine DBA: Eric Stine Architect Inc. |
| 548958 | 100 | 1 | Marino General Contracting Ltd. |
| 573042 | 0 | 1 | H Demolition & Excavation Ltd. |
| 573117 | 0 | 1 | Geowest Developments Ltd |

## 12. Intarsia Design Ltd.

- **Norm key:** `intarsiadesign`
- **Distinct roots:** 4
- **Distinct companies:** 4
- **PI applicant rows:** 460
- **Winning canonical company:** 548668 — Intarsia Design Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548668 | 460 | 1 | Intarsia Design Ltd. |
| 572985 | 0 | 1 | Canadian Excavation Ltd |
| 573023 | 0 | 1 | All right trucking 99 |
| 573103 | 0 | 1 | 2nd storey |

## 13. space smart home design ltd.

- **Norm key:** `spacesmarthomedesign`
- **Distinct roots:** 4
- **Distinct companies:** 4
- **PI applicant rows:** 565
- **Winning canonical company:** 549011 — space smart home design ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549011 | 569 | 1 | space smart home design ltd. |
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 549159 | 18 | 1 | Kingsman Excavating Ltd. |
| 573015 | 0 | 1 | Bhullar Excavating and Demolition |

## 14. Yan Building Design Studio Ltd.

- **Norm key:** `yanbuildingdesignstudio`
- **Distinct roots:** 4
- **Distinct companies:** 4
- **PI applicant rows:** 316
- **Winning canonical company:** 589 — Merry Gao DBA: Yan Building Design Studio Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 589 | 316 | 1 | Merry Gao DBA: Yan Building Design Studio Ltd. |
| 7866 | 6 | 1 | Dasheng Development LTD. DBA: Development |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 572969 | 0 | 1 | Excavating Ltd. |

## 15. 88 Homes LTD.

- **Norm key:** `88homes`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 289
- **Winning canonical company:** 43 — Kanwal Sekhon DBA: 88 Homes LTD.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 43 | 289 | 1 | Kanwal Sekhon DBA: 88 Homes LTD. |
| 572970 | 0 | 1 | New Golden Developments FRONT UNIT 3084 E 8th Av - One Family Dwelling |
| 573015 | 0 | 1 | Bhullar Excavating and Demolition |

## 16. Architelier Architecture & Real Estate Consulting Inc.

- **Norm key:** `architelierarchitecture&realestateconsulting`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 162
- **Winning canonical company:** 2837 — Danny Wong DBA: Architelier Architecture & Real Estate Consulting Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 2837 | 162 | 1 | Danny Wong DBA: Architelier Architecture & Real Estate Consulting Inc. |
| 6606 | 2 | 1 | Gregory Yu DBA: KHY Contracting Inc. |
| 573054 | 0 | 1 | Real Estate Consulting Inc. |

## 17. Architrix Design Studio Inc.

- **Norm key:** `architrixdesignstudio`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 707
- **Winning canonical company:** 548952 — Architrix Design Studio

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548952 | 707 | 1 | Architrix Design Studio |
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 18. BC Home Drafting & Consulting Ltd.

- **Norm key:** `bchomedrafting&consulting`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 452
- **Winning canonical company:** 548882 — BC Home Drafting & Consulting Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548882 | 460 | 1 | BC Home Drafting & Consulting Ltd. |
| 549025 | 60 | 1 | Eyco Building Group Ltd. |
| 573095 | 0 | 1 | Consulting Ltd. |

## 19. Darcy Jones Architecture Inc.

- **Norm key:** `darcyjonesarchitecture`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 44
- **Winning canonical company:** 548774 — D'Arcy Jones Architecture

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548774 | 44 | 1 | D'Arcy Jones Architecture |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 573018 | 0 | 1 | Merola Construction Inc "Combustible projections or roof soffits on an exposing building face shall not project to less than .45m from the property line and shall be in compliance with VBBL 2014 9.10.15.5 |

## 20. Design Professional

- **Norm key:** `designprofessional`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 24
- **Winning canonical company:** 548748 — Design Professional

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548748 | 24 | 1 | Design Professional |
| 573011 | 0 | 1 | East West Excavating Ltd |
| 573086 | 0 | 1 | Star Enterprise Inc |

## 21. Dore Design & Development

- **Norm key:** `doredesign&development`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 40
- **Winning canonical company:** 332 — Bradley Dore DBA: Dore Design & Development

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 332 | 40 | 1 | Bradley Dore DBA: Dore Design & Development |
| 7866 | 6 | 1 | Dasheng Development LTD. DBA: Development |
| 572969 | 0 | 1 | Excavating Ltd. |

## 22. Edit Studios Inc.

- **Norm key:** `editstudios`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 148
- **Winning canonical company:** 549057 — Gibraltar Holdings Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549057 | 334 | 1 | Gibraltar Holdings Ltd. |
| 1166 | 148 | 1 | Janay Koldingnes DBA: Edit Studios Inc. |
| 6524 | 38 | 1 | Rodolfo Perez DBA: PB Management Group Inc. |

## 23. Eriksberg Engineering Ltd.

- **Norm key:** `eriksbergengineering`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 33
- **Winning canonical company:** 8127 — Erik Watson-Hurthig DBA: Eriksberg Engineering Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8127 | 33 | 1 | Erik Watson-Hurthig DBA: Eriksberg Engineering Ltd. |
| 10136 | 4 | 1 | Kern BSG Management Ltd. DBA: Kern BSG Management Ltd. |
| 134429 | 0 | 1 | Pro-Can Construction Group Corp. |

## 24. Evoke International Design

- **Norm key:** `evokeinternationaldesign`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 82
- **Winning canonical company:** 1015 — David Nicolay DBA: Evoke International Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1015 | 82 | 1 | David Nicolay DBA: Evoke International Design |
| 573036 | 0 | 1 | Natural Balance Development Group Inc |
| 573041 | 0 | 1 | Octiscapes Site Services Ltd |

## 25. Home Line Construction & Renovations Ltd

- **Norm key:** `homelineconstruction&renovations`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 66
- **Winning canonical company:** 1882 — Alex Chuy DBA: Home Line Construction & Renovations Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1882 | 66 | 1 | Alex Chuy DBA: Home Line Construction & Renovations Ltd |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 573112 | 0 | 1 | JVT EXCAVATING AND DEMOLITION LTD |

## 26. Jack McDonald Residential Design

- **Norm key:** `jackmcdonaldresidentialdesign`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 210
- **Winning canonical company:** 1065 — Jack McDonald DBA: Jack McDonald Residential Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1065 | 210 | 1 | Jack McDonald DBA: Jack McDonald Residential Design |
| 572991 | 0 | 1 | Admiral Operations 5. The entire building to be sprinklered to 2 x NFPA 13D Address and suite numbers assigned as per approved plans for Fire and Emergency response. The address numbers are to be posted on the building and to be visible from the street and the suite numbers are to be posted at the s |
| 573065 | 0 | 1 | TRG Construction Corp |

## 27. Leonic Investments Inc.

- **Norm key:** `leonicinvestments`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 4
- **Winning canonical company:** 548996 — Canadian Excavating Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 9888 | 4 | 1 | Jason Li DBA: Leonic Investments Inc. |
| 573026 | 0 | 1 | Canadian Excavating Ltd. No recycling required. DP-2021-00392 - to develop new 6-storey Multiple Dwelling BP-2021-06347 - Notes: 1. Notice of Demolition must be provided to District Building Inspector 24 hours in advance of demolition by calling 3-1-1 or 604-873-7000 outside Vancouver 2. All work mu |

## 28. Lung Designs Group Ltd.

- **Norm key:** `lungdesigns`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 501
- **Winning canonical company:** 22 — Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 22 | 501 | 1 | Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd. |
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 572971 | 0 | 1 | East West Excavating 2022 Ltd |

## 29. McCuaig and Associates Engineering Ltd.

- **Norm key:** `mccuaigandassociatesengineering`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 1146
- **Winning canonical company:** 2539 — McCuaig and Associates Engineering Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 2539 | 1146 | 1 | McCuaig and Associates Engineering Ltd. |
| 573049 | 0 | 1 | Solid General Contractors Inc |
| 573088 | 0 | 1 | Spring Up Construction Ltd |

## 30. Radiant City Architecture

- **Norm key:** `radiantcityarchitecture`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 85
- **Winning canonical company:** 548646 — Concrete Cashmere Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548646 | 120 | 1 | Concrete Cashmere Ltd. |
| 2050 | 86 | 1 | Ron Bijok DBA: Radiant City Architecture |
| 573130 | 0 | 1 | Lalli Development (2011) Ltd |

## 31. Simplex Home Design Ltd.

- **Norm key:** `simplexhomedesign`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 120
- **Winning canonical company:** 612 — Tej Singh DBA: Simplex Home Design Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 612 | 120 | 1 | Tej Singh DBA: Simplex Home Design Ltd. |
| 302080 | 1 | 1 | VGC VANCOUVER GENERAL CONTRACTORS INC |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 32. Strand Holdings Ltd.

- **Norm key:** `strand`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 42
- **Winning canonical company:** 548937 — MWL Demolition

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548937 | 290 | 1 | MWL Demolition |
| 8440 | 42 | 1 | Isaac Kaay DBA: Strand Holdings Ltd. |
| 573035 | 0 | 1 | Hans Demolition and Excavating Ltd. |

## 33. Thorson Consulting CP

- **Norm key:** `thorsonconsultingcp`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 111
- **Winning canonical company:** 548937 — MWL Demolition

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548937 | 290 | 1 | MWL Demolition |
| 548974 | 111 | 1 | Thorson Consulting CP |
| 7819 | 12 | 1 | William Lee DBA: Fairway Recycle Group |

## 34. Tyko Development Ltd.

- **Norm key:** `tykodevelopment`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 152
- **Winning canonical company:** 549053 — Tyko Development Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549053 | 152 | 1 | Tyko Development Ltd. |
| 572962 | 0 | 1 | DEMOLITION LTD |
| 572969 | 0 | 1 | Excavating Ltd. |

## 35. Westpoint Design & Development Ltd.

- **Norm key:** `westpointdesign&development`
- **Distinct roots:** 3
- **Distinct companies:** 3
- **PI applicant rows:** 828
- **Winning canonical company:** 84 — Mike Chu DBA: Westpoint Design & Development Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 84 | 828 | 1 | Mike Chu DBA: Westpoint Design & Development Ltd. |
| 7866 | 6 | 1 | Dasheng Development LTD. DBA: Development |
| 573042 | 0 | 1 | H Demolition & Excavation Ltd. |

## 36. Absolute Design Services

- **Norm key:** `absolutedesignservices`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 132
- **Winning canonical company:** 1216 — Abel Wan DBA: Absolute Design Services

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1216 | 132 | 1 | Abel Wan DBA: Absolute Design Services |
| 573119 | 0 | 1 | Tsuan Wan Construction Co Ltd |

## 37. Act III Design & Construction Ltd.

- **Norm key:** `actiiidesign&construction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 548949 — construction

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548949 | 76 | 1 | construction |
| 106 | 4 | 1 | Craig Strand DBA: Act III Design & Construction Ltd. |

## 38. Active Earth Engineering Ltd.

- **Norm key:** `activeearthengineering`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 18
- **Winning canonical company:** 549142 — Active Earth Engineering Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549142 | 18 | 1 | Active Earth Engineering Ltd. |
| 573010 | 0 | 1 | TX Contracting Ltd |

## 39. AH Design Group Inc

- **Norm key:** `ahdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 106
- **Winning canonical company:** 1007 — Albert Hui DBA: AH Design Group Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1007 | 106 | 1 | Albert Hui DBA: AH Design Group Inc |
| 573123 | 0 | 1 | Investment |

## 40. AIR CONDITIONING LTD

- **Norm key:** `airconditioning`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 37
- **Winning canonical company:** 2951 — Milani Plumbing Heating & Air Conditioning Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 2951 | 36 | 1 | Milani Plumbing Heating & Air Conditioning Ltd |
| 302880 | 1 | 1 | 2907 51ST AVE E VANCOUVER, BC V5S 1R6S K REFRIGERATION & AIR CONDITIONING LTD |

## 41. Alex Lerner Landscape Design

- **Norm key:** `alexlernerlandscapedesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 9690 — Alex Lerner Landscape Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9690 | 4 | 1 | Alex Lerner Landscape Design |
| 10236 | 2 | 1 | Hyland Landscapes Ltd. DBA: Hyland Landscapes |

## 42. Aliki Gladwin & Associates Inc.

- **Norm key:** `alikigladwin&associates`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 12
- **Winning canonical company:** 548991 — Aliki Gladwin & Associates

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548991 | 12 | 1 | Aliki Gladwin & Associates |
| 573031 | 0 | 1 | Associates Inc. |

## 43. Ambleside Developments Ltd.

- **Norm key:** `amblesidedevelopments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 50
- **Winning canonical company:** 549026 — Ambleside Developments Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549026 | 50 | 1 | Ambleside Developments Ltd. |
| 573106 | 0 | 1 | B2. SHAMBHU N. BISWAS |

## 44. Averra Developments Inc.

- **Norm key:** `averradevelopments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 26
- **Winning canonical company:** 4153 — Gavin Mcleod DBA: Averra Developments Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4153 | 26 | 1 | Gavin Mcleod DBA: Averra Developments Inc. |
| 549159 | 18 | 1 | Kingsman Excavating Ltd. |

## 45. AWNING MANUFACTURING LTD

- **Norm key:** `awningmanufacturing`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 302094 — CANHWA SIGNS & AWNING MANUFACTURING LTD

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 302094 | 1 | 1 | CANHWA SIGNS & AWNING MANUFACTURING LTD |
| 302461 | 1 | 1 | 146 - 11782 RIVER RD RICHMOND, BC V6X 1Z7CANHWA SIGNS & AWNING MANUFACTURING LTD |

## 46. Bright Coast Homes Ltd.

- **Norm key:** `brightcoasthomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 74
- **Winning canonical company:** 266 — Wendy Gee DBA: Bright Coast Homes Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 266 | 74 | 1 | Wendy Gee DBA: Bright Coast Homes Ltd. |
| 573081 | 0 | 1 | B2. Shamsul Alam Shikder |

## 47. b Squared Architecture Inc.

- **Norm key:** `bsquaredarchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 78
- **Winning canonical company:** 191 — Brian Billingsley DBA: b Squared Architecture Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 191 | 78 | 1 | Brian Billingsley DBA: b Squared Architecture Inc. |
| 573093 | 0 | 1 | Billingsley Construction Ltd |

## 48. My House Design/Build Civic Liaison

- **Norm key:** `buildcivicliaison`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 16
- **Winning canonical company:** 549179 — Design / Build

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549179 | 98 | 1 | Design / Build |
| 572978 | 0 | 1 | Build Team Ltd |

## 49. Cadlab Design Inc.

- **Norm key:** `cadlabdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 394
- **Winning canonical company:** 7 — TIMOTHY TSE DBA: Cadlab Design Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7 | 394 | 1 | TIMOTHY TSE DBA: Cadlab Design Inc. |
| 572996 | 0 | 1 | CAN WON CONSULTING LTD |

## 50. Camphora Engineering

- **Norm key:** `camphoraengineering`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 282
- **Winning canonical company:** 548946 — Camphora Engineering

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548946 | 282 | 1 | Camphora Engineering |
| 548702 | 4 | 1 | Syncra Construction |

## 51. Casa Loma Homes Ltd.

- **Norm key:** `casalomahomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 12
- **Winning canonical company:** 6790 — Raymond Dorner DBA: Casa Loma Homes Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6790 | 12 | 1 | Raymond Dorner DBA: Casa Loma Homes Ltd. |
| 3648 | 8 | 1 | Amritpal Kang DBA: Bigcity Excavation Ltd. |

## 52. CDH Design Ltd

- **Norm key:** `cdhdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 34
- **Winning canonical company:** 4260 — Cameron Hardisty DBA: CDH Design Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4260 | 34 | 1 | Cameron Hardisty DBA: CDH Design Ltd |
| 573048 | 0 | 1 | De Jager Homes Ltd |

## 53. Claire Saksun Studio

- **Norm key:** `clairesaksunstudio`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 20
- **Winning canonical company:** 7951 — Claire Saksun DBA: Claire Saksun Studio

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7951 | 20 | 1 | Claire Saksun DBA: Claire Saksun Studio |
| 8248 | 4 | 1 | Nick Hone DBA: Coterra Contracting Ltd. |

## 54. CMGT Construction Group Ltd.

- **Norm key:** `cmgtconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 20
- **Winning canonical company:** 548996 — Canadian Excavating Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 787 | 20 | 1 | Ian Fung DBA: CMGT Construction Group Ltd. |

## 55. Coastal Green

- **Norm key:** `coastalgreen`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 4782 — James Woodcock DBA: Traslo West Contracting

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4782 | 2 | 1 | James Woodcock DBA: Traslo West Contracting |
| 8704 | 2 | 1 | 1372443 B.C. Ltd. DBA: Coastal Green |

## 56. collabor8 Architecture + Design (BC) Inc.

- **Norm key:** `collabor8architecturedesignbc`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 548854 — Collabor8 Architecture + Design Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548854 | 10 | 1 | Collabor8 Architecture + Design Inc. |
| 3365 | 2 | 1 | Peter Kollar DBA: collabor8 Architecture + Design (BC) Inc. |

## 57. Contoura Architecture Ltd.

- **Norm key:** `contouraarchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 238
- **Winning canonical company:** 175 — Vipul Chauhan DBA: Contoura Architecture Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 175 | 238 | 1 | Vipul Chauhan DBA: Contoura Architecture Ltd. |
| 9609 | 4 | 1 | Arash Afshar Ahmadi DBA: Design |

## 58. Counterpoint Projects Inc

- **Norm key:** `counterpointprojects`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 548731 — Counterpoint Interiors Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548731 | 140 | 1 | Counterpoint Interiors Inc. |
| 8429 | 4 | 1 | Alison Lau DBA: Counterpoint Projects Inc |

## 59. CP13 Developments & Consulting Ltd

- **Norm key:** `cp13developments&consulting`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 30
- **Winning canonical company:** 6157 — Christopher Paul DBA: CP13 Developments & Consulting Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6157 | 30 | 1 | Christopher Paul DBA: CP13 Developments & Consulting Ltd |
| 10221 | 12 | 1 | Sachan Mandair DBA: Dema Developments Ltd. |

## 60. CPOS DEVELOPMENT CORP

- **Norm key:** `cposdevelopment`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 5
- **Winning canonical company:** 302189 — CPOS DEVELOPMENT CORP

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 302189 | 5 | 1 | CPOS DEVELOPMENT CORP |
| 573000 | 0 | 1 | MVP Group Recycle Limited |

## 61. David S Mah Architect

- **Norm key:** `davidsmaharchitect`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 212
- **Winning canonical company:** 32 — David Mah DBA: David S Mah Architect

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 32 | 212 | 1 | David Mah DBA: David S Mah Architect |
| 3389 | 4 | 1 | Kim Mah DBA: Koko Construction Ltd |

## 62. Dicata Group

- **Norm key:** `dicata`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 24
- **Winning canonical company:** 2954 — Arash Tavakoli DBA: Dicata Group

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 2954 | 24 | 1 | Arash Tavakoli DBA: Dicata Group |
| 573079 | 0 | 1 | Dicata Construction Ltd |

## 63. DNVS Design Inc.

- **Norm key:** `dnvsdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 42
- **Winning canonical company:** 548976 — DNVS Design Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548976 | 42 | 1 | DNVS Design Inc |
| 549067 | 8 | 1 | Solaris Properties Inc. |

## 64. DRAFTING, DESIGN & PROJECT MANAGEMENT

- **Norm key:** `draftingdesign&projectmanagement`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 9436 — JONAS HIBO DBA: DRAFTING, DESIGN & PROJECT MANAGEMENT

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9436 | 2 | 1 | JONAS HIBO DBA: DRAFTING, DESIGN & PROJECT MANAGEMENT |
| 573029 | 0 | 1 | PROJECT MANAGEMENT |

## 65. Dreamworks Home Design Ltd.

- **Norm key:** `dreamworkshomedesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 34
- **Winning canonical company:** 3051 — Pavit Randhawa DBA: Dreamworks Home Design Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3051 | 34 | 1 | Pavit Randhawa DBA: Dreamworks Home Design Ltd. |
| 573129 | 0 | 1 | B2. O.Y. Lee |

## 66. Dwell Living

- **Norm key:** `dwellliving`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 30
- **Winning canonical company:** 6008 — Shannon Nystrom DBA: Dwell Living

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6008 | 30 | 1 | Shannon Nystrom DBA: Dwell Living |
| 573067 | 0 | 1 | 1078258 BC LTD |

## 67. (Eddie) Yan Ho Choy

- **Norm key:** `eddieyanhochoy`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 14
- **Winning canonical company:** 8097 — (Eddie) Yan Ho Choy

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8097 | 14 | 1 | (Eddie) Yan Ho Choy |
| 573055 | 0 | 1 | Van-City Excavating Ltd |

## 68. EHY Properties Inc

- **Norm key:** `ehyproperties`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 42
- **Winning canonical company:** 236 — EHY Properties Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 236 | 42 | 1 | EHY Properties Inc |
| 572998 | 0 | 1 | B King Construction Ltd |

## 69. Empire West Construction Ltd.

- **Norm key:** `empirewestconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 100
- **Winning canonical company:** 338 — Ken Yee DBA: Empire West Construction Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 338 | 100 | 1 | Ken Yee DBA: Empire West Construction Ltd. |
| 572956 | 0 | 1 | J B Siteworks Inc. |

## 70. Encore Collection

- **Norm key:** `encorecollection`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 76
- **Winning canonical company:** 6922 — Mike Bhayana DBA: Encore Collection

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6922 | 76 | 1 | Mike Bhayana DBA: Encore Collection |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 71. Formwerks Architectural Incorporated

- **Norm key:** `formwerksarchitectural`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 234
- **Winning canonical company:** 548664 — Formwerks Architectural Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548664 | 266 | 1 | Formwerks Architectural Inc. |
| 573086 | 0 | 1 | Star Enterprise Inc |

## 72. Fusion Projects Ltd.

- **Norm key:** `fusionprojects`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 421
- **Winning canonical company:** 548699 — Fusion Projects

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548699 | 427 | 1 | Fusion Projects |
| 5569 | 2 | 1 | Jason Kidd DBA: Fusion Project Management Ltd |

## 73. GeoSpace Consulting Inc.

- **Norm key:** `geospaceconsulting`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 22
- **Winning canonical company:** 575 — Sodhi Dadral DBA: Sodhi Development Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 575 | 104 | 1 | Sodhi Dadral DBA: Sodhi Development Ltd. |
| 5173 | 22 | 1 | Parveen Aggarwal DBA: GeoSpace Consulting Inc. |

## 74. GHL Consultants, a part of J.S. Held

- **Norm key:** `ghlconsultantsapartofjsheld`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 18
- **Winning canonical company:** 548779 — GHL Consultants Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548779 | 335 | 1 | GHL Consultants Ltd |
| 573109 | 0 | 1 | Townline Construction Inc |

## 75. Gracorp Properties LP

- **Norm key:** `gracorpproperties`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 18
- **Winning canonical company:** 9311 — Gracorp Properties LP

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9311 | 18 | 1 | Gracorp Properties LP |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 76. Gradual Architecture

- **Norm key:** `gradualarchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 221
- **Winning canonical company:** 548655 — Gradual Architecture

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548655 | 224 | 1 | Gradual Architecture |
| 3695 | 4 | 1 | Alkarim Bapoo DBA: Asante Construction Ltd |

## 77. Granity Homes Ltd

- **Norm key:** `granityhomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 6
- **Winning canonical company:** 9114 — RDP Johal DBA: Granity Homes Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9114 | 6 | 1 | RDP Johal DBA: Granity Homes Ltd |
| 573062 | 0 | 1 | Contracting Ltd |

## 78. Henderson Development (Canada) Ltd

- **Norm key:** `hendersondevelopmentcanada`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 8
- **Winning canonical company:** 3369 — Jim Carney DBA: Henderson Development (Canada) Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3369 | 8 | 1 | Jim Carney DBA: Henderson Development (Canada) Ltd |
| 573043 | 0 | 1 | Atlantic Electric Ltd |

## 79. Heritage Design & Construction Management Inc.

- **Norm key:** `heritagedesign&constructionmanagement`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 18
- **Winning canonical company:** 431 — Chen Shun Chew DBA: Heritage Design & Construction Management Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 431 | 18 | 1 | Chen Shun Chew DBA: Heritage Design & Construction Management Inc. |
| 8668 | 4 | 1 | Jaspreet Singh Gill DBA: Construction Management |

## 80. iFortune Homes Inc.

- **Norm key:** `ifortunehomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 548942 — iFortune Homes Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548942 | 4 | 1 | iFortune Homes Inc. |
| 573011 | 0 | 1 | East West Excavating Ltd |

## 81. Inspired Design

- **Norm key:** `inspireddesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 47
- **Winning canonical company:** 4423 — Diana Kwan DBA: Inspired Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4423 | 47 | 1 | Diana Kwan DBA: Inspired Design |
| 572969 | 0 | 1 | Excavating Ltd. |

## 82. Interior Space Enterprises Inc.

- **Norm key:** `interiorspace`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 6943 — Andy Chan DBA: Interior Space Enterprises Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6943 | 4 | 1 | Andy Chan DBA: Interior Space Enterprises Inc. |
| 572974 | 0 | 1 | H&D CONSTRUCTION GROUP |

## 83. Iredale Achitecture

- **Norm key:** `iredaleachitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 548658 — Novacom Building Partners

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548658 | 18 | 1 | Novacom Building Partners |
| 9413 | 2 | 1 | Albert Lam DBA: Iredale Achitecture |

## 84. Iredale Architecture

- **Norm key:** `iredalearchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 112
- **Winning canonical company:** 302191 — IREDALE ARCHITECTURE

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 302191 | 112 | 1 | IREDALE ARCHITECTURE |
| 548720 | 20 | 1 | Vestacon Limited |

## 85. J & R Katz Design + Architecture Inc.

- **Norm key:** `j&rkatzdesignarchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 128
- **Winning canonical company:** 548736 — J & R Katz Design + Architecture Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548736 | 128 | 1 | J & R Katz Design + Architecture Inc. |
| 573021 | 0 | 1 | R Katz Design + Architecture Inc. |

## 86. Jamie Banfield Design

- **Norm key:** `jamiebanfielddesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 20
- **Winning canonical company:** 1394 — Jessica Hanley DBA: AW Kennedy Construction

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1394 | 20 | 1 | Jessica Hanley DBA: AW Kennedy Construction |
| 8721 | 20 | 1 | Utoo Candar DBA: Jamie Banfield Design |

## 87. JBI Development Ltd.

- **Norm key:** `jbidevelopment`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 230
- **Winning canonical company:** 548735 — JBI Development Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548735 | 230 | 1 | JBI Development Ltd. |
| 548996 | 60 | 1 | Canadian Excavating Ltd |

## 88. JCJL Enterprises Inc.

- **Norm key:** `jcjl`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 6
- **Winning canonical company:** 5614 — June McIntyre DBA: JCJL Enterprises Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 5614 | 6 | 1 | June McIntyre DBA: JCJL Enterprises Inc. |
| 573100 | 0 | 1 | BON VAYAGE RENOVATION LIMITED |

## 89. Jensen Hughes Consulting

- **Norm key:** `jensenhughesconsulting`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 81
- **Winning canonical company:** 2257 — Jensen Hughes Consulting Canada ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 2257 | 290 | 1 | Jensen Hughes Consulting Canada ltd. |
| 417 | 81 | 1 | Gordon Richards DBA: Jensen Hughes Consulting |

## 90. JETFINITY DESIGN BUILD INC

- **Norm key:** `jetfinitydesignbuild`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 114
- **Winning canonical company:** 4325 — Jet X. Liang DBA: JETFINITY DESIGN BUILD INC

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4325 | 114 | 1 | Jet X. Liang DBA: JETFINITY DESIGN BUILD INC |
| 572996 | 0 | 1 | CAN WON CONSULTING LTD |

## 91. Jim Construction Ltd

- **Norm key:** `jimconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 52
- **Winning canonical company:** 7699 — Jimmy Gill DBA: Jim Construction Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7699 | 52 | 1 | Jimmy Gill DBA: Jim Construction Ltd |
| 573092 | 0 | 1 | Jewel Mini Excavating Ltd |

## 92. JNS Developments Ltd

- **Norm key:** `jnsdevelopments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 28
- **Winning canonical company:** 548996 — Canadian Excavating Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 8096 | 28 | 1 | JNS Developments Ltd DBA: JNS Developments Ltd |

## 93. Joy Design Limited

- **Norm key:** `joydesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 82
- **Winning canonical company:** 4505 — Helen Han DBA: Joy Design Limited

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4505 | 82 | 1 | Helen Han DBA: Joy Design Limited |
| 572984 | 0 | 1 | MCCM Residential Ltd |

## 94. JSS DEVELOPMENT LTD

- **Norm key:** `jssdevelopment`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 56
- **Winning canonical company:** 7845 — Aman Sidhu DBA: JSS DEVELOPMENT LTD

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7845 | 56 | 1 | Aman Sidhu DBA: JSS DEVELOPMENT LTD |
| 573063 | 0 | 1 | Metro Contracting Ltd |

## 95. Karra Turner Design

- **Norm key:** `karraturnerdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 92
- **Winning canonical company:** 62 — Harbhajan Karra DBA: Grand Van Homes Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 62 | 139 | 1 | Harbhajan Karra DBA: Grand Van Homes Inc |
| 7156 | 92 | 1 | Mumta Karra DBA: Karra Turner Design |

## 96. Kasian Architecture + Interiors

- **Norm key:** `kasianarchitectureinteriors`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 18
- **Winning canonical company:** 548643 — Reotech Construction Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548643 | 376 | 1 | Reotech Construction Ltd |
| 548850 | 28 | 1 | Kasian Architecture |

## 97. Kenorah Design + Build

- **Norm key:** `kenorahdesignbuild`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 50
- **Winning canonical company:** 549179 — Design / Build

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549179 | 98 | 1 | Design / Build |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 98. KERR DESIGN BUILD

- **Norm key:** `kerrdesignbuild`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 30
- **Winning canonical company:** 548667 — Kerr Construction

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548667 | 84 | 1 | Kerr Construction |
| 5589 | 30 | 1 | Leo Chester DBA: KERR DESIGN BUILD |

## 99. Killarney Development Ltd.

- **Norm key:** `killarneydevelopment`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 100
- **Winning canonical company:** 1816 — Killarney Development Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1816 | 100 | 1 | Killarney Development Ltd. |
| 549159 | 18 | 1 | Kingsman Excavating Ltd. |

## 100. lehail construction ltd

- **Norm key:** `lehailconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 26
- **Winning canonical company:** 8374 — Harminder Lehail DBA: lehail construction ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8374 | 26 | 1 | Harminder Lehail DBA: lehail construction ltd |
| 572957 | 0 | 1 | G N A CONTRACTING LTD |

## 101. Linhan Design & Interiors Co.

- **Norm key:** `linhandesign&interiors`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 44
- **Winning canonical company:** 548864 — Linhan Design & Interiors Co.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548864 | 46 | 1 | Linhan Design & Interiors Co. |
| 572997 | 0 | 1 | Interiors Co. |

## 102. LMDG Building Code Consultant

- **Norm key:** `lmdgbuildingcodeconsultant`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 252
- **Winning canonical company:** 548765 — LMDG Building Code Consultants Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548765 | 684 | 1 | LMDG Building Code Consultants Ltd. |
| 420 | 252 | 1 | Michael Van Blokland DBA: LMDG Building Code Consultant |

## 103. L-Squared Design Ltd.

- **Norm key:** `lsquareddesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 24
- **Winning canonical company:** 8407 — David Lin DBA: L-Squared Design Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8407 | 24 | 1 | David Lin DBA: L-Squared Design Ltd. |
| 572976 | 0 | 1 | Everbright Construction Inc |

## 104. Mann Bros Construction Ltd.

- **Norm key:** `mannbrosconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 44
- **Winning canonical company:** 5949 — Raj Mann DBA: Mann Bros Construction Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 5949 | 44 | 1 | Raj Mann DBA: Mann Bros Construction Ltd. |
| 573015 | 0 | 1 | Bhullar Excavating and Demolition |

## 105. Mason Investments Ltd.

- **Norm key:** `masoninvestments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 8019 — fred saafan DBA: West Coast Industrial Care Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8019 | 2 | 1 | fred saafan DBA: West Coast Industrial Care Ltd |
| 9642 | 2 | 1 | Laura Smith DBA: Mason Investments Ltd. |

## 106. McLeod Bovell Modern Houses

- **Norm key:** `mcleodbovellmodernhouses`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 8
- **Winning canonical company:** 549086 — Mcleod Bovell Modern Houses

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549086 | 8 | 1 | Mcleod Bovell Modern Houses |
| 572995 | 0 | 1 | Headwater Management Ltd |

## 107. Mechanical Systems Ltd.

- **Norm key:** `mechanicalsystems`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 9
- **Winning canonical company:** 1506 — BMS Plumbing & Mechanical Systems Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1506 | 14 | 1 | BMS Plumbing & Mechanical Systems Ltd. |
| 302447 | 1 | 1 | 1263 CLARK DR VANCOUVER, BC V5L 3K6BMS PLUMBING & MECHANICAL SYSTEMS LTD |

## 108. Metric Architecture

- **Norm key:** `metricarchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 110
- **Winning canonical company:** 723 — metric architecture

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 723 | 111 | 1 | metric architecture |
| 627 | 18 | 1 | Ken Cheung DBA: K E Concepts 2001 Ltd |

## 109. Mezetta Homes Ltd.

- **Norm key:** `mezettahomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 14
- **Winning canonical company:** 3253 — Andy Samra DBA: Mezetta Homes Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3253 | 14 | 1 | Andy Samra DBA: Mezetta Homes Ltd. |
| 572964 | 0 | 1 | Mezetta Homes Ltd ******THIS PERMIT HAS BEEN ISSUED UNDER THE REQUIREMENTS OF VBBL 2014 AND THE GREEN HOMES PROGRAM****** |

## 110. Mizan Developments Ltd

- **Norm key:** `mizandevelopments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 91
- **Winning canonical company:** 662 — Kazeem Bapoo DBA: Mizan Developments Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 662 | 91 | 1 | Kazeem Bapoo DBA: Mizan Developments Ltd |
| 572999 | 0 | 1 | Excavation Inc |

## 111. MJS Design Ltd

- **Norm key:** `mjsdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 256
- **Winning canonical company:** 229 — Marina Lok DBA: MJS Design Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 229 | 256 | 1 | Marina Lok DBA: MJS Design Ltd |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 112. Modern Style Homes Ltd.

- **Norm key:** `modernstylehomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 30
- **Winning canonical company:** 6752 — Jasbir Sekhon DBA: Modern Style Homes Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6752 | 30 | 1 | Jasbir Sekhon DBA: Modern Style Homes Ltd. |
| 549159 | 18 | 1 | Kingsman Excavating Ltd. |

## 113. m squared Architecture Inc.

- **Norm key:** `msquaredarchitecture`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 191
- **Winning canonical company:** 548643 — Reotech Construction Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548643 | 376 | 1 | Reotech Construction Ltd |
| 1242 | 191 | 1 | Michael McNaught DBA: m squared Architecture Inc. |

## 114. Nada Awadi Architecture + Design

- **Norm key:** `nadaawadiarchitecturedesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 1916 — Kenton Lepp DBA: Lepp Construction Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 1916 | 10 | 1 | Kenton Lepp DBA: Lepp Construction Inc. |
| 9921 | 4 | 1 | Nada Awadi DBA: Nada Awadi Architecture + Design |

## 115. Novell Design Build

- **Norm key:** `novelldesignbuild`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 64
- **Winning canonical company:** 894 — Laurel James DBA: Novell Design Build

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 894 | 64 | 1 | Laurel James DBA: Novell Design Build |
| 573102 | 0 | 1 | Novell Construction Ltd |

## 116. Office of McFarlane Biggar Architects + Designers Inc

- **Norm key:** `officeofmcfarlanebiggararchitectsdesigners`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 10
- **Winning canonical company:** 549153 — Office of McFarlane Biggar

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549153 | 18 | 1 | Office of McFarlane Biggar |
| 548840 | 4 | 1 | Office of Mcfarlane Biggar Architects + Designers |

## 117. On Side Restoration

- **Norm key:** `onsiderestoration`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 34
- **Winning canonical company:** 548676 — Onside Restorations

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548676 | 90 | 1 | Onside Restorations |
| 548814 | 0 | 1 | Onside Restoration Services Inc |

## 118. Orchid Homes

- **Norm key:** `orchidhomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 32
- **Winning canonical company:** 6594 — Darbara Aujla DBA: Orchid Homes

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6594 | 32 | 1 | Darbara Aujla DBA: Orchid Homes |
| 573011 | 0 | 1 | East West Excavating Ltd |

## 119. OTC Project BT Limited

- **Norm key:** `otcprojectbt`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 36
- **Winning canonical company:** 2254 — Michael Burak DBA: OTC Project BT Limited

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 2254 | 36 | 1 | Michael Burak DBA: OTC Project BT Limited |
| 573122 | 0 | 1 | Bobby Brar of JBS Services Ltd. Related to: DP-2022-00705: Response to Conditions BP-2023-01243: In Review |

## 120. Owner Builder

- **Norm key:** `ownerbuilder`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 24
- **Winning canonical company:** 548773 — Owner Builder

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548773 | 90 | 1 | Owner Builder |
| 169 | 76 | 1 | Sukhwinder Gill DBA: Gill's Construction Ltd. |

## 121. PACIFIC HOMES LTD.

- **Norm key:** `pacifichomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 27
- **Winning canonical company:** 5966 — Amarjit Grewal DBA: PACIFIC HOMES LTD.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 5966 | 27 | 1 | Amarjit Grewal DBA: PACIFIC HOMES LTD. |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 122. Paramjit sidhu

- **Norm key:** `paramjitsidhu`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 30
- **Winning canonical company:** 7741 — Paramjeet Sidhu DBA: Singh Development LTD

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7741 | 96 | 1 | Paramjeet Sidhu DBA: Singh Development LTD |
| 3939 | 30 | 1 | Paramjit Sidhu |

## 123. Patkau Architects Inc

- **Norm key:** `patkauarchitects`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 14
- **Winning canonical company:** 549078 — Patkau Architects Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549078 | 14 | 1 | Patkau Architects Inc |
| 7225 | 2 | 1 | Jason Hart DBA: Hart Tipton Construction Ltd |

## 124. Paul Tarjan Architect

- **Norm key:** `paultarjanarchitect`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 8
- **Winning canonical company:** 6509 — Tommy Nham DBA: Paul Tarjan Architect

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6509 | 8 | 1 | Tommy Nham DBA: Paul Tarjan Architect |
| 572972 | 0 | 1 | Keller Construction Ltd |

## 125. Pennyfarthing Development

- **Norm key:** `pennyfarthingdevelopment`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 54
- **Winning canonical company:** 548962 — Pennyfarthing Development

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548962 | 54 | 1 | Pennyfarthing Development |
| 572994 | 0 | 1 | Kare Environmental Ltd |

## 126. Pioneer Code Consultants Ltd.

- **Norm key:** `pioneercodeconsultants`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 146
- **Winning canonical company:** 790 — Michael Meszaros DBA: Pioneer Code Consultants Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 790 | 146 | 1 | Michael Meszaros DBA: Pioneer Code Consultants Ltd. |
| 549009 | 79 | 1 | Vanwell Homes |

## 127. Polaron Energy Corp.

- **Norm key:** `polaronenergy`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 60
- **Winning canonical company:** 3207 — Tommy Wong DBA: Polaron Energy Corp.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3207 | 60 | 1 | Tommy Wong DBA: Polaron Energy Corp. |
| 573080 | 0 | 1 | Haven Solar Inc |

## 128. Prior Street Phase I LP

- **Norm key:** `priorstreetphasei`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 12
- **Winning canonical company:** 548937 — MWL Demolition

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548937 | 290 | 1 | MWL Demolition |
| 9362 | 12 | 1 | Sebastian Stewart DBA: Prior Street Phase I LP |

## 129. Psquare Engineering and Construction Ltd.

- **Norm key:** `psquareengineeringandconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 48
- **Winning canonical company:** 548996 — Canadian Excavating Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548996 | 60 | 1 | Canadian Excavating Ltd |
| 549108 | 48 | 1 | Psquare Engineering and Construction Ltd. |

## 130. Puzzle Developments Ltd

- **Norm key:** `puzzledevelopments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 90
- **Winning canonical company:** 985 — Varinder Grewal DBA: Puzzle Developments Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 985 | 90 | 1 | Varinder Grewal DBA: Puzzle Developments Ltd |
| 3648 | 8 | 1 | Amritpal Kang DBA: Bigcity Excavation Ltd. |

## 131. Raffaele and Associates

- **Norm key:** `raffaeleandassociates`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 156
- **Winning canonical company:** 279 — Raffaele & Associates DBA: Raffaele and Associates

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 279 | 156 | 1 | Raffaele & Associates DBA: Raffaele and Associates |
| 573035 | 0 | 1 | Hans Demolition and Excavating Ltd. |

## 132. Read Jones Christoffersen Ltd.

- **Norm key:** `readjoneschristoffersen`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 161
- **Winning canonical company:** 9801 — Read Jones Christoffersen Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9801 | 161 | 1 | Read Jones Christoffersen Ltd. |
| 666 | 2 | 1 | DARYL HEPPNER DBA: Polycrete Restorations Ltd. |

## 133. Realint Projects Ltd.

- **Norm key:** `realintprojects`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 9828 — Realint Projects Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9828 | 2 | 1 | Realint Projects Ltd. |
| 573064 | 0 | 1 | Sun Capital Corporate Construction Inc |

## 134. Refine and Design Custom Homes and Renovations

- **Norm key:** `refineanddesigncustomhomesandrenovations`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 6
- **Winning canonical company:** 7312 — Refine and Design Real Estate Inc. DBA: Refine and Design Custom Homes and Renovations

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7312 | 6 | 1 | Refine and Design Real Estate Inc. DBA: Refine and Design Custom Homes and Renovations |
| 573110 | 0 | 1 | Design Real Estate Inc |

## 135. Roseland Development INC.

- **Norm key:** `roselanddevelopment`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 24
- **Winning canonical company:** 548689 — Roseland Development INC.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548689 | 24 | 1 | Roseland Development INC. |
| 572962 | 0 | 1 | DEMOLITION LTD |

## 136. Scott Posno Design

- **Norm key:** `scottposnodesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 240
- **Winning canonical company:** 548660 — Scott Posno Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548660 | 240 | 1 | Scott Posno Design |
| 548996 | 60 | 1 | Canadian Excavating Ltd |

## 137. Sharma Ent Ltd (DBA www.selhomes.ca )

- **Norm key:** `sharmaentdbawwwselhomesca`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 16
- **Winning canonical company:** 6237 — Deepak Sharma DBA: Sharma Ent Ltd (DBA www.selhomes.ca )

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 6237 | 16 | 1 | Deepak Sharma DBA: Sharma Ent Ltd (DBA www.selhomes.ca ) |
| 573028 | 0 | 1 | 1167245 B C Ltd |

## 138. Sian Group Investments Inc.

- **Norm key:** `sianinvestments`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 54
- **Winning canonical company:** 3199 — Mukhtiar Sian DBA: Sian Group Investments Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3199 | 54 | 1 | Mukhtiar Sian DBA: Sian Group Investments Inc. |
| 549159 | 18 | 1 | Kingsman Excavating Ltd. |

## 139. Skima Holdings LTD

- **Norm key:** `skima`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 10056 — Inderjit Mann DBA: Skima Holdings LTD

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 10056 | 4 | 1 | Inderjit Mann DBA: Skima Holdings LTD |
| 573039 | 0 | 1 | A Excavating Ltd |

## 140. Splyce Design

- **Norm key:** `splycedesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 16
- **Winning canonical company:** 3298 — Nigel Parish DBA: Splyce Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3298 | 16 | 1 | Nigel Parish DBA: Splyce Design |
| 572981 | 0 | 1 | PTL Contracting Ltd |

## 141. SSDG Interiors Inc.

- **Norm key:** `ssdginteriors`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 153
- **Winning canonical company:** 8085 — SSDG

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8085 | 162 | 1 | SSDG |
| 548725 | 0 | 1 | SSDG Interiors Inc |

## 142. Success Realty & Insurance Ltd.

- **Norm key:** `successrealty&insurance`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 9479 — Jordan Eng DBA: Success Realty & Insurance Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9479 | 2 | 1 | Jordan Eng DBA: Success Realty & Insurance Ltd. |
| 573027 | 0 | 1 | Insurance Ltd. |

## 143. Suvic Holdings Inc.

- **Norm key:** `suvic`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 74
- **Winning canonical company:** 181 — Rey Lim DBA: Suvic Holdings Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 181 | 74 | 1 | Rey Lim DBA: Suvic Holdings Inc. |
| 3389 | 4 | 1 | Kim Mah DBA: Koko Construction Ltd |

## 144. Table Architecture Collective

- **Norm key:** `tablearchitecturecollective`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 67
- **Winning canonical company:** 862 — Bill Uhrich DBA: Table Architecture Collective

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 862 | 68 | 1 | Bill Uhrich DBA: Table Architecture Collective |
| 572973 | 0 | 1 | Disher Construction Ltd |

## 145. Tamanna Design Group Ltd.

- **Norm key:** `tamannadesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 100
- **Winning canonical company:** 548663 — Tamanna Design Group Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548663 | 100 | 1 | Tamanna Design Group Ltd. |
| 572975 | 0 | 1 | Indra Construction Ltd |

## 146. Tangerine Developments Ltd. & Punia Homes Ltd.

- **Norm key:** `tangerinedevelopments&puniahomes`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 32
- **Winning canonical company:** 257 — Aman Dhillon DBA: Tangerine Developments Ltd. & Punia Homes Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 257 | 32 | 1 | Aman Dhillon DBA: Tangerine Developments Ltd. & Punia Homes Ltd. |
| 573035 | 0 | 1 | Hans Demolition and Excavating Ltd. |

## 147. Tarlochan (Tony) Paul

- **Norm key:** `tarlochantonypaul`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 6
- **Winning canonical company:** 8853 — Tarlochan (Tony) Paul

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 8853 | 6 | 1 | Tarlochan (Tony) Paul |
| 573015 | 0 | 1 | Bhullar Excavating and Demolition |

## 148. TD Studio Inc.

- **Norm key:** `tdstudio`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 207
- **Winning canonical company:** 248 — Vikram Tiku DBA: TD Studio Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 248 | 207 | 1 | Vikram Tiku DBA: TD Studio Inc. |
| 572999 | 0 | 1 | Excavation Inc |

## 149. TKA+D Architecture & Design

- **Norm key:** `tkadarchitecture&design`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 549118 — TKA+D

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549118 | 10 | 1 | TKA+D |
| 9609 | 4 | 1 | Arash Afshar Ahmadi DBA: Design |

## 150. Trillium Projects

- **Norm key:** `trilliumprojects`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 548937 — MWL Demolition

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548937 | 290 | 1 | MWL Demolition |
| 9785 | 4 | 1 | Michael Brown DBA: Trillium Projects |

## 151. Union Allied Capital Corporation

- **Norm key:** `unionalliedcapital`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 302189 — CPOS DEVELOPMENT CORP

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 302189 | 5 | 1 | CPOS DEVELOPMENT CORP |
| 9582 | 2 | 1 | Union Allied Capital Corporation |

## 152. Upward Construction and Renovations ltd.

- **Norm key:** `upwardconstructionandrenovations`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 2
- **Winning canonical company:** 9964 — Justin Jarvo DBA: Upward Construction and Renovations ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9964 | 2 | 1 | Justin Jarvo DBA: Upward Construction and Renovations ltd. |
| 573057 | 0 | 1 | RENOVATION LTD. |

## 153. Vancouver Drafting

- **Norm key:** `vancouverdrafting`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 28
- **Winning canonical company:** 7166 — David Domoslai DBA: Vancouver Drafting

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7166 | 28 | 1 | David Domoslai DBA: Vancouver Drafting |
| 573089 | 0 | 1 | Amini Construction Inc |

## 154. Vancouver General Contractors

- **Norm key:** `vancouvergeneralcontractors`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 129
- **Winning canonical company:** 585 — VANCOUVER GENERAL CONTRACTORS

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 585 | 129 | 1 | VANCOUVER GENERAL CONTRACTORS |
| 302080 | 1 | 1 | VGC VANCOUVER GENERAL CONTRACTORS INC |

## 155. Vancouver Renewable Energy Co-operative

- **Norm key:** `vancouverrenewableenergyoperative`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 42
- **Winning canonical company:** 453 — Vancouver Renewable Energy Co-operative

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 453 | 42 | 1 | Vancouver Renewable Energy Co-operative |
| 573058 | 0 | 1 | Vancouver Renewable Energy Cooperative |

## 156. Vancouver School Board

- **Norm key:** `vancouverschoolboard`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 19
- **Winning canonical company:** 549022 — Vancouver School Board

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549022 | 24 | 1 | Vancouver School Board |
| 573035 | 0 | 1 | Hans Demolition and Excavating Ltd. |

## 157. Venture Pacific Construction Management Ltd.

- **Norm key:** `venturepacificconstructionmanagement`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 28
- **Winning canonical company:** 549005 — Venture Pacific Construction Management Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 549005 | 28 | 1 | Venture Pacific Construction Management Ltd. |
| 573037 | 0 | 1 | J&R Excavation and Demolition Ltd |

## 158. VictorEric Design Group Ltd.

- **Norm key:** `victorericdesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 270
- **Winning canonical company:** 548724 — Victoreric design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548724 | 282 | 1 | Victoreric design |
| 572982 | 0 | 1 | Weihan Design Inc. "Combustible projections or roof soffits on an exposing building face shall not project to less than .45m from the property line and shall be in compliance with VBBL 2014 9.10.15.5 |

## 159. VIKA Holdings Inc

- **Norm key:** `vika`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 4
- **Winning canonical company:** 9657 — VIKA Holdings Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 9657 | 4 | 1 | VIKA Holdings Inc |
| 573035 | 0 | 1 | Hans Demolition and Excavating Ltd. |

## 160. VMS Management Inc.

- **Norm key:** `vmsmanagement`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 14
- **Winning canonical company:** 418 — Vivian Fung DBA: VMS Management Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 418 | 14 | 1 | Vivian Fung DBA: VMS Management Inc. |
| 573009 | 0 | 1 | YSL Construction Inc. |

## 161. Wesgroup Properties LP

- **Norm key:** `wesgroupproperties`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 58
- **Winning canonical company:** 548881 — Wesgroup Properties

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548881 | 62 | 1 | Wesgroup Properties |
| 573019 | 0 | 1 | Wesgroup Contracting Ltd |

## 162. WideUse Construction & Design

- **Norm key:** `wideuseconstruction&design`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 54
- **Winning canonical company:** 340 — Yan Cheung DBA: WideUse Construction & Design

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 340 | 54 | 1 | Yan Cheung DBA: WideUse Construction & Design |
| 9609 | 4 | 1 | Arash Afshar Ahmadi DBA: Design |

## 163. Willow Spring Construction

- **Norm key:** `willowspringconstruction`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 8
- **Winning canonical company:** 548879 — Willow Spring Construction BC Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548879 | 24 | 1 | Willow Spring Construction BC Ltd. |
| 548869 | 0 | 1 | Willow Spring Construction |

## 164. Winston Chong Architects Inc

- **Norm key:** `winstonchongarchitects`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 28
- **Winning canonical company:** 3082 — Winston Chong DBA: Winston Chong Architects Inc

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 3082 | 28 | 1 | Winston Chong DBA: Winston Chong Architects Inc |
| 6890 | 2 | 1 | City Wide Building Inc. DBA: City Wide Building INC. |

## 165. WNDR Architecture + Design Inc.

- **Norm key:** `wndrarchitecturedesign`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 44
- **Winning canonical company:** 7794 — Jason Hyare DBA: Laneside Homes Ltd

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 7794 | 46 | 1 | Jason Hyare DBA: Laneside Homes Ltd |
| 7152 | 44 | 1 | WNDR Architecture + Design Inc. |

## 166. Woodbine Builders Ltd.

- **Norm key:** `woodbinebuilders`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 122
- **Winning canonical company:** 548669 — Woodbine Builders Ltd.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 548669 | 122 | 1 | Woodbine Builders Ltd. |
| 572956 | 0 | 1 | J B Siteworks Inc. |

## 167. Zed Studio Inc.

- **Norm key:** `zedstudio`
- **Distinct roots:** 2
- **Distinct companies:** 2
- **PI applicant rows:** 56
- **Winning canonical company:** 4061 — Christian Zane Erickson DBA: Zed Studio Inc.

| Root ID | Total projects | Companies | Names |
|--------:|---------------:|----------:|-------|
| 4061 | 56 | 1 | Christian Zane Erickson DBA: Zed Studio Inc. |
| 548830 | 10 | 1 | Dakota Holdings Ltd. |

---

## Excluded groups (manual review only)

- **Heating Co Ltd** (`heating`): 8 roots — generic_name
- **Developer / Designer** (`designer`): 5 roots — generic_name
- **DEVELOPMENT LTD** (`development`): 5 roots — generic_name
- **Construction Company** (`construction`): 4 roots — generic_name
- **Excavating Ltd.** (`excavating`): 3 roots — generic_name
- **Aikid Design/Management Inc.** (`management`): 3 roots — generic_name
- **NEON** (`neon`): 3 roots — generic_name
- **SLA Inc.** (`sla`): 3 roots — generic_name
- **Aecom** (`aecom`): 2 roots — generic_name
- **Ambient** (`ambient`): 2 roots — generic_name
- **Axomm Construction** (`axommconstruction`): 2 roots — generic_name
- **Bizzarri Construction** (`bizzarriconstruction`): 2 roots — generic_name
- **Kenorah Design/Build Ltd.** (`build`): 2 roots — generic_name
- **CONSTRUCTION MANAGEMENT INC** (`constructionmanagement`): 2 roots — generic_name
- **Danson Fong** (`dansonfong`): 2 roots — generic_name
- **Demolition Ltd** (`demolition`): 2 roots — generic_name
- **DESIGN LTD** (`design`): 2 roots — generic_name
- **firstonsite** (`firstonsite`): 2 roots — generic_name
- **Intracorp** (`intracorp`): 2 roots — generic_name
- **Kindred Construction Ltd** (`kindredconstruction`): 2 roots — generic_name
- **Lanefab** (`lanefab`): 2 roots — generic_name
- **Nexus Construction** (`nexusconstruction`): 2 roots — generic_name
- **PHSA** (`phsa`): 2 roots — generic_name
- **PHW HOMES INC.** (`phwhomes`): 2 roots — generic_name
- **Proscenium** (`proscenium`): 2 roots — generic_name
- **PTL Design** (`ptldesign`): 2 roots — generic_name
- **PWA** (`pwa`): 2 roots — generic_name
- **RAAW Design** (`raawdesign`): 2 roots — generic_name
- **Renovation & Construction** (`renovation&construction`): 2 roots — generic_name
- **Renovations Ltd.** (`renovations`): 2 roots — generic_name
- **SIGNS LTD** (`signs`): 2 roots — generic_name
- **ValiDesign** (`validesign`): 2 roots — generic_name
- **VLM Construction** (`vlmconstruction`): 2 roots — generic_name
- **WRAPS** (`wraps`): 2 roots — generic_name