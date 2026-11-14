# Contacting roof owners

How to use `outputs/contact_worksheet.csv` to contact the owners or occupants of roofs suspected to be asbestos-cement, one at a time. This is practical guidance, not legal advice. Have a lawyer or privacy consultant confirm it before you start, especially before any commercial use.

## Grant-led workflow (start here)

Grants are what makes owners act, and deadlines are what make removal companies buy. So work from the grant down:

1. **Map page → Grants tab.** Each scheme's card shows:
   - its status: OPEN with days left, upcoming, or closed ("prepare for the next call");
   - how many candidate roofs may be eligible, and how sure the owner type is (confirmed by your lookup, guessed from public evidence, unknown);
   - a funnel: to identify → owner identified → contacted → interested / not interested.
2. **Show eligible roofs.** The list is ranked by score: roof size × asbestos score × owner-type certainty × solar fit (schemes that also fund panels) × contact stage. You can:
   - filter by owner certainty, size, asbestos result, solar panels, stage, town, or search;
   - click a town to open that roof on the map;
   - use **Show on map** to highlight the filtered roofs, or **Export CSV** to download them.
3. **Confirm owners where it matters most.** `outputs/grant_lists/<scheme>_owner_lookups.csv` holds the top 50 eligible roofs per scheme with no confirmed owner type, no owner and no company on site. The Agenzia delle Entrate form fields come first. Type the result in `contact_worksheet.csv`:
   - `owner_type` (e.g. "azienda agricola", "srl", "privato", "comune") overrides the guess;
   - `owner_from_visura` marks the owner as identified.
4. **Contact and record.** `contact_date` / `contact_method` move a roof to "contacted". An `outcome` containing "interessato" (or "preventivo", "sopralluogo") marks it interested, and "non interessato" marks it not interested. `do_not_contact` removes it from every list.
5. **Rebuild:** `python scripts/contact_worksheet.py && python scripts/aerial_map.py`. Lists, funnels and scores follow what you typed.

`outputs/grant_lists/<scheme>.csv` is the full ranked list per scheme. While a scheme is closed, the list is your head start for its next call.

## What the worksheet gives you

For each candidate roof (score >= 0.7, at least 500 m2, Brescia pilot):

| Column | Source | Licence |
|---|---|---|
| `status`, `model_score` | ARPA Lombardia 2020 map + aerial model | see DATA.md |
| `comune_code`, `foglio`, `particella`, `parcel_ref` | Agenzia delle Entrate cadastral parcels (INSPIRE WFS) | CC BY 4.0 |
| `address` | Overture addresses (Italian countrywide address data) | see Overture |
| `business_name`, `business_category`, `business_website`, `business_address` | Overture places: the best listing at the building (inside the footprint, or within 30 m and nearer to this roof than to any other building) | CDLA Permissive 2.0 |
| `n_businesses` | how many businesses are listed at the building (up to 5) | |
| `n_nearby_businesses`, `nearby_business_name`, `nearby_business_website` | the first business listed next door: within 30 m, but at another building. An adjoining building comes first, then the nearest. On a multi-building site this is often the right contact | |
| `owner_from_visura` ... `notes` | empty, for you to fill in. Kept when the worksheet is regenerated (see below) | |

What the worksheet does not give you:
- **Owner names.** They are not open data. Look them up one property at a time with a *visura per immobile* from the Agenzia delle Entrate, using `comune_code`, `foglio` and `particella` (log in with SPID, CIE or CNS).
- **Phone numbers and emails.** Left out on purpose; see below.

The business at a building is often a tenant, not the owner. It is still usually the right first contact for an industrial roof.

## What you type is kept

`scripts/contact_worksheet.py` never wipes the hand-filled columns. On each run it:

- reads the existing worksheet as text and carries `owner_from_visura` ... `notes` (and any column you added) over to the new file, when both `roof_id` and `parcel_ref` still match;
- first saves the old file as `outputs/contact_worksheet.backup-YYYYmmdd-HHMMSS.csv`, whenever it held anything typed by hand;
- appends rows it cannot carry over (the roof is no longer a candidate, or its parcel changed) to `outputs/contact_worksheet_orphans.csv`, with the reason. Check that file after each run and copy the rows back by hand.

You can edit the worksheet in a spreadsheet. A file re-saved with `;` or tab separators, or in Windows-1252, is read as it is. If the header row is lost (no `roof_id` column), the script and the map stop at once and write nothing. Columns you add are kept.

## An objection belongs to the property

Fill `do_not_contact` with anything (for example `yes` and the date). Empty, `0`, `no` and `false` mean "may contact". The mark is never lost by regenerating the worksheet. After a run of `scripts/contact_worksheet.py`, the worksheet and the map mark the same candidate roofs (the map applies a newly typed objection to the whole parcel at once; the worksheet does so on its next run):

- Every objection spreads to all current rows on the same parcel, with a note saying which roof it came from.
- When a row with `do_not_contact` cannot be carried over (the roof left the candidates, or its parcel changed), the objection still reaches the roof's new row and every row on its old and new parcel. This is repeated on every run from the orphans file. If it reaches no current row, the script prints a warning on every run.
- In the map, such roofs show a red "Do not contact" banner. So does a roof that is no longer a candidate but has an objection in the orphans file.
- To withdraw an objection, clear `do_not_contact` on every row of that parcel in `contact_worksheet.csv`, and on the rows about that roof or parcel in `contact_worksheet_orphans.csv`. Otherwise the next run puts it back.
- Log a second contact in the same row. A duplicated row is moved to the orphans file on the next run.
- If you edit `contact_worksheet_orphans.csv`, keep its header row. Without it the script and the map stop.

To make the company step forget a business as well, list its website domain or VAT number in `outputs/company_suppress.csv` (columns `domain` and/or `vat`; you create this file). The domain also covers its subdomains. `scripts/enrich_companies.py` then deletes what it stored about it and never looks it up again; `scripts/contact_worksheet.py` drops its listing from the worksheet and from `roof_occupants.csv`; and the listing disappears from the map, with its company record (until the worksheet is rerun the map shows it as "Business listing hidden: do not contact"). A listed domain also covers the same business's other websites (same VAT number). After editing the list, run `enrich_companies.py`, then `contact_worksheet.py`, then `aerial_map.py`, in that order. A listing with no website cannot be put on the list. To lift a suppression, remove the line and run the three scripts again; the company record returns on the next `enrich_companies.py`, which goes online for it.

## The map panel

Click a roof in `outputs/aerial_map.html` to see what is publicly known about who is there. The panel shows, in this order:

| Section | Content | Source |
|---|---|---|
| Property | address, cadastral reference (with a copy button), other candidate roofs on the same parcel | Overture addresses; Agenzia delle Entrate parcels |
| Businesses at this building | up to 5 listings: name, category, website, address | Overture places (`outputs/roof_occupants.csv`) |
| Next door (within 30 m) | businesses listed at a neighbouring building, marked "adjoining building" when the two buildings touch | same |
| Other public sources | environmental permit holder within 200 m, named OpenStreetMap site within 150 m, a company found on the same parcel; a "best guess" line when no business is listed at the building | Regione Lombardia AIA permits; OpenStreetMap |
| Company public record | VAT number (Partita IVA), legal name and form, registered office, REA | the business's own website, checked against the EU VIES service (`outputs/company_register.csv`) |
| Owner (from your own visura) | what you typed in the worksheet | you |
| Look up | links for one-at-a-time lookups: web search, Agenzia delle Entrate (visura), business register, VIES | |

How to read the company record:

- **It says who occupies the building, not who owns it.** The owner still comes from a visura.
- **Registered office** is shown only for companies that VIES confirms (S.r.l., S.p.A., S.n.c. and so on). It is where a formal letter should go.
- **Sole traders and unknown legal forms:** the legal name is often the trader's own name, and the VIES address can be a home address. The address is left out and the panel marks the record "treat as personal data". The rules of thumb below apply to it.
- **"Not in the EU VIES register"** usually means the business does not trade within the EU. It does not mean the number is fake.
- **Notes on a record:** "Legal name differs from the listing" means the name on the website does not look like the listed business, so check it is the same one. "Chain or shared website" means several differently named businesses list that website, so the record may be the franchisor's. "Read over a connection with an invalid certificate" means the site's certificate could not be verified.
- No phone numbers, emails, PEC addresses or names of directors are kept or shown, by design. A "website" that is really a WhatsApp or Telegram link, or that carries a phone number or e-mail address, is dropped.

On a roof marked `do_not_contact` the panel hides the owner name, the notes and the outcome, and leaves the roof out of the map's search. It also hides what could name the person: company records of sole traders at or next door to that roof, and business listings that are linked to a sole trader's record or share two or more words with that sole trader's legal name. A listing with no company record is still shown, even if its name is a person's. If it has a website, put that on the suppression list above. Hidden listings show as "Business listing hidden: do not contact", on that roof and on its neighbours. Listings of companies stay. The "Other public sources" section is hidden there too, and the same permit holder or site name is hidden on other roofs. This hides more than strictly needed, on purpose.

## From parcel to a contact: four routes

**1. Open registries that name the company at a location (automatic, in the worksheet).** `known_company` takes the first available of:

| Source | Rule |
|---|---|
| Overture places | the best business listing at the building (inside the footprint, or within 30 m and nearer to this roof than to any other building) |
| AIA environmental permits (Regione Lombardia) | industry, intensive livestock or waste plant within 200 m |
| OpenStreetMap | named farmyard, industrial site or operator within 150 m |
| Any of the above | anywhere inside the roof's cadastral parcel |

Coverage in the Brescia pilot: 42% of the 1,258 candidate roofs (Overture at the building 193, OpenStreetMap 180, AIA permit 102, same parcel 58). A further 136 candidates have a business listed next door only (`nearby_business_name`); it is not counted here.

A permit holder or an OpenStreetMap name can be a farm run by one person. Treat such a name like a sole trader's (rule 8 below).

**2. Owner from the cadastre (manual).** `outputs/visura_queue.csv` lists the 50 largest candidate parcels with no company found, no owner typed yet and no objection. It is rebuilt on every run, so do not type into it: put the result in `owner_from_visura`, `owner_type` and `notes` of `contact_worksheet.csv`, which keeps what you type. The parcel then leaves the queue.
- For each, request a *visura per immobile* (town code + foglio + particella) on the Agenzia delle Entrate site (SPID).
- If the owner is a company, look up its registered office and VAT number in the business register (registroimprese.it). The first search is free; the full company report costs a few euros.
- If the owner is a private person, do not build a list. Contact them only by an individual letter with the art. 14 notice.

**3. Manual identification.** `street_view` opens Google Street View at the roof: company signs on gates and facades are often readable. `company_search` runs a web search for the company found, to reach its own website and published business contacts.

**4. Channels that already have a relationship with the owners.** No personal data is needed on your side for any of these:
- **Municipalities:** an asbestos awareness campaign to owners. Councils have a public-interest reason and their own data, and you can offer the map.
- **Farmers' associations:** Coldiretti, Confagricoltura and CIA Brescia. Their agricultural service centres (CAA) manage members' farm records and can inform members about removal and Agrisolare.
- **Business associations:** Confindustria Brescia, Confartigianato, CNA.
- **Inbound:** adverts geo-targeted to the industrial zones around candidate roofs, unaddressed leaflets in those zones, and a landing page ("Is my roof asbestos? Which grants apply?"). Owners who reply have given consent.

Not acceptable: matching owners' tax codes to phone numbers, buying phone or email lists, or scraping personal contact details. That is what the Garante sanctioned in the Realmaps case.

## Rules of thumb

1. **Contact businesses, not private individuals, where you can.** A private person's name taken from a cadastral record is personal data. Building lists of owners for marketing is what the Garante sanctioned in the Realmaps case (data matched with phone numbers and sold to estate agents; agencies fined for telemarketing without consent).
2. **One property at a time, by letter or in person.** Do not build a bulk list of owner names, and do not run a mass mailing.
3. **No cold emails, no cold calls from scraped numbers.** In Italy, unsolicited marketing emails need prior consent, even to companies. Marketing calls are not allowed to numbers on the opt-out register (Registro Pubblico delle Opposizioni). Certified email (PEC) addresses from INI-PEC are for official communications, not marketing.
4. **Tell them where the information came from (GDPR art. 14).** Data source, your purpose, how long you keep their data, and how to object. The letter template below includes this.
5. **Say "suspected", never "your roof is asbestos".** The ARPA map and the model can be wrong or out of date. Only an inspection or a lab test confirms asbestos.
6. **Honour "no".** If someone objects, mark `do_not_contact` and delete their personal data, apart from what you need to remember not to contact them again. The mark is kept when the worksheet is regenerated and shows in the map (see "An objection belongs to the property").
7. **Keep the worksheet and the map private.** They stay in `outputs/` (not committed) and should not be shared. The same goes for `roof_occupants.csv`, `roof_addresses.csv` and `company_register.csv`.
8. **A sole trader is a person.** A company's legal name and registered office are not personal data. A sole trader's name is. Treat it like an owner's name from a visura.

## Useful facts for the conversation

- In Lombardy, owners of buildings with asbestos-cement must report them to the local health authority (ATS), using the form known as NA/1.
- Regional and national grant schemes have funded removal, for example the Parco Agrisolare scheme for farm buildings (removal plus solar). Check which calls are open before mentioning them.
- The aerial photo the model saw is the AGEA 2024 orthophoto. Bring a printout: it makes the conversation concrete.

## Letter template (Italian)

Replace everything in [square brackets].

> [Nome Cognome]
> [Indirizzo] · [email] · [telefono]
>
> Spett.le [ragione sociale / Proprietario dell'immobile]
> [indirizzo dell'immobile]
>
> [Luogo], [data]
>
> **Oggetto: copertura dell'edificio in [indirizzo] – possibile presenza di cemento-amianto**
>
> Gentile [Titolare / Proprietario],
>
> mi chiamo [Nome Cognome] e sto lavorando a [descrizione breve e veritiera del progetto, ad es. "un progetto di ricerca sul monitoraggio delle coperture in cemento-amianto in provincia di Brescia"].
>
> Dalla mappatura pubblica delle coperture in cemento-amianto di Regione Lombardia / ARPA Lombardia [oppure: dall'analisi delle ortofoto pubbliche AGEA 2024] risulta che la copertura dell'edificio in [indirizzo] (foglio [foglio], particella [particella]) **potrebbe** essere in cemento-amianto. Si tratta di un'indicazione, non di un accertamento: solo un sopralluogo o un'analisi di laboratorio possono confermarlo.
>
> [Motivo del contatto, ad es. "Le scrivo per sapere se la copertura è già stata verificata o bonificata, e se le interessano informazioni sui contributi disponibili per la rimozione."]
>
> Se preferisce non essere ricontattato, me lo comunichi a [email/telefono] e non la contatterò più.
>
> Cordiali saluti,
> [Nome Cognome]
>
> ---
> *Informativa (art. 14 Reg. UE 2016/679).* Titolare del trattamento: [Nome Cognome, indirizzo, email]. Dati trattati: [denominazione/nome del proprietario o dell'occupante, indirizzo dell'immobile, riferimenti catastali]. Fonti: cartografia catastale e visura catastale (Agenzia delle Entrate), mappatura delle coperture in cemento-amianto (Regione Lombardia / ARPA Lombardia), ortofoto AGEA 2024, elenchi pubblici di attività commerciali. Finalità: [finalità]. Base giuridica: legittimo interesse [da verificare con un consulente]. Conservazione: [periodo, ad es. 12 mesi o fino a opposizione]. I dati non sono comunicati a terzi. Lei può chiedere accesso, rettifica, cancellazione e opporsi al trattamento scrivendo a [email], e può presentare reclamo al Garante per la protezione dei dati personali (www.garanteprivacy.it).
