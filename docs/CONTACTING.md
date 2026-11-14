# Contacting roof owners

How to use `outputs/contact_worksheet.csv` to contact the owners or occupants of roofs suspected to be asbestos-cement, one at a time. This is practical guidance, not legal advice. Have a lawyer or privacy consultant confirm it before you start, especially before any commercial use.

## What the worksheet gives you

For each candidate roof (score >= 0.7, at least 500 m2, Brescia pilot):

| Column | Source | Licence |
|---|---|---|
| `status`, `model_score` | ARPA Lombardia 2020 map + aerial model | see DATA.md |
| `comune_code`, `foglio`, `particella`, `parcel_ref` | Agenzia delle Entrate cadastral parcels (INSPIRE WFS) | CC BY 4.0 |
| `address` | Overture addresses (Italian countrywide address data) | see Overture |
| `business_name`, `business_category`, `business_website` | Overture places, within 30 m of the roof | CDLA Permissive 2.0 |
| `owner_from_visura` ... `notes` | empty, for you to fill in | |

What the worksheet does not give you:
- **Owner names.** They are not open data. Look them up one property at a time with a *visura per immobile* from the Agenzia delle Entrate, using `comune_code`, `foglio` and `particella` (log in with SPID, CIE or CNS).
- **Phone numbers and emails.** Left out on purpose; see below.

The business at a building is often a tenant, not the owner. It is still usually the right first contact for an industrial roof.

## From parcel to a contact: four routes

**1. Open registries that name the company at a location (automatic, in the worksheet).** `known_company` takes the first available of:

| Source | Rule |
|---|---|
| Overture places | business within 30 m of the roof |
| AIA environmental permits (Regione Lombardia) | industry, intensive livestock or waste plant within 200 m |
| OpenStreetMap | named farmyard, industrial site or operator within 150 m |
| Any of the above | anywhere inside the roof's cadastral parcel |

Coverage in the Brescia pilot: 48% of the 1,258 candidate roofs.

**2. Owner from the cadastre (manual).** `outputs/visura_queue.csv` lists the parcels of the 50 largest candidate roofs with no company found.
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
6. **Honour "no".** If someone objects, mark `do_not_contact` and delete their personal data, apart from what you need to remember not to contact them again.
7. **Keep the worksheet private.** It stays in `outputs/` (not committed) and should not be shared.

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
