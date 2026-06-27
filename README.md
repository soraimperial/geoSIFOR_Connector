# geoSIFOR_Connector
Allows to quickly manage saved geoSIFOR endpoints (WFS, WMS, ...) in QGIS, without having to go through all the QGIS dialogues everytime you want to add a new one

## What is GeoSIFOR, and why this plugin exists


[SIFOR](https://www.sgifr.gov.pt/), and its geospatial counterpart [GeoSIFOR](https://geosifor.sgifr.gov.pt/), is the information system behind Portugal's SGIFR (Sistema de Gestão Integrada de Fogos Rurais — the Integrated Rural Fire Management System), created by Decreto-Lei n.º 82/2021 and coordinated by [AGIF](https://agif.pt/pt) (Agência para a Gestão Integrada de Fogos Rurais), which is legally responsible for the system's public information disclosure. It brings together, under one platform and harnessing an interoperable plataform (PLIS), the data that the different entities involved in SGIFR — AGIF, ICNF, DGT, GNR, ANEPC, and others — each produce and maintain about
rural fires: burned area history, fuel/vegetation cover, the wildland-urban interface, operational resources, active fire occurrences, orthophotography, and more.
If you work with wildfire risk, planning, or suppression in Portugal, GeoSIFOR is very likely where the layer you need already lives.

Even though it wildly simplifies life compared to whatever crawling was needed before, it still generates the tedium of having to add layers, one a a time, to QGIS.
That tedium is the entire reason this plugin exists. GeoSIFOR's own viewer is a capable way to look at this data, but every layer behind it is its own WFS, WMS, WMTS, ArcGIS REST, or plain JSON service, each needing its own URL, its own auth setup, and its own trip through QGIS's "Add Layer" dialogs — multiplied by however many layers you actually
need for your analysis.

This plugin doesn't change what GeoSIFOR is or how it works; it just removes the one-URL-at-a-time friction of getting that same data into QGIS, with your credential, your folders, and your own shortlist of the services you actually use.

> [!Warning]  
> Built independently to make working with GeoSIFOR's services in QGIS faster. Not an official AGIF/SGIFR tool.


## What it solves

GeoSIFOR's catalogue is "GeoSIFOR’s web catalogue is a JavaScript-based viewer (not a crawlable index). Each product's service URL (WFS/WMS/REST/GeoJSON) has to be found by hand, once, in the viewer — but the URLs are stable afterward. Doing this through QGIS's normal "Add Connection" dialog means repeating the whole flow per service, even though many of them share one entity, one product family, and one Basic Auth credential.

This plugin removes the repeated cost by allowing to save and manage connections found by the user:
- Paste a URL once, label it, mark it public/restricted, file it into a   folder — saved forever in QGIS settings (per profile).
- Set up the Basic Auth credential once, via QGIS's own Authentication  Manager (encrypted, not stored by this plugin).
- Tick whichever endpoints you want this session (or check a whole  folder at once), click "Add selected to map."
- If no credential is set up, restricted entries are skipped with a  clear message; public ones still work.
- Search/filter, star favourites, and save named profiles (e.g.  "Operational Decision", "Fuels") for quickly recalling a set of  endpoints you check together often.

## Install

1. Find your QGIS profile's plugin folder and copy the geosifor_connector folder there.
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
2. Use the install .zip option in Plugin Manager.
3. [Maybe in the future] Install it through the Plugin Repo.

The Plugin will be displayed under the "Web" tab or, alternatively, somewhere in your toolbars.
<img width="1815" height="861" alt="image" src="https://github.com/user-attachments/assets/c54b571b-fd1a-46a4-ba5c-57d300fde55c" />

## First-time setup

1. The "Shared credential" section is expanded by default the very first   time you open the panel (it auto-collapses afterward once something's
   configured, to leave more room for the endpoint list). Use the   dropdown to pick a credential you've already created in QGIS — no
   need to re-enter anything you've already saved. If you don't have one   yet, click the **+** button next to the dropdown, which opens QGIS's
   own authentication config editor (the same one used everywhere else in   QGIS).
2. Click "Add new endpoint" to expand that section (it starts collapsed,   same idea), and add each known endpoint — label, URL (copied from the
   GeoSIFOR viewer), service type, folder (optional), and whether it's   public.
3. From then on: tick what you want, click "Add selected to map."

Both sections remember whether you left them expanded or collapsed, the same way QGIS's own collapsible panels do.

<img width="1086" height="832" alt="image-1" src="https://github.com/user-attachments/assets/aa3e21e7-fdcf-484d-b984-c631fbdd2e47" />

## How to get credentials and endpoints

1. If you have a government-approved GeoSIFOR account: go to [PLIS](plis.sgifr.gov.pt/) and log in with your own credentials. Under the Token menu, generate a token —
you're offered 3 auth types, and Basic is the recommended one for this plugin. Paste that token straight into QGIS's own authentication config editor (the + button next to the credential dropdown above) as a Basic Auth login.

<img width="1772" height="933" alt="image-2" src="https://github.com/user-attachments/assets/a69f2afe-4107-47ee-89f0-90400d6c4009" />


3. To find the actual service URLs, go to the Serviços tab on the same site, pick the service you want, scroll right, and click "Aceder." Copy the "Endereço PLIS" shown there — that's the URL to paste into this plugin's "Add new endpoint" form. The service type is part of that same URL (e.g. /arcgis/rest/services/GNR/... means REST) — match it to the "Service type" dropdown when adding the endpoint.

<img width="1747" height="806" alt="image-3" src="https://github.com/user-attachments/assets/31854bc5-85b5-4f66-b07b-56fc4a0b0229" />

4. If you don't have a government-approved account: you can stillreach GeoSIFOR's public endpoints. Go to [GeoSIFOR](geosifor.sgifr.gov.pt), click the
cogwheel/info button on whichever layer you want, and copy its service URL from there. Only layers marked Public are reachable this way —
mark the endpoint as "Public" in the add-endpoint form, since no credential will work for it regardless.

<img width="1662" height="912" alt="image-4" src="https://github.com/user-attachments/assets/2d047e46-b7d7-4826-b364-44ed59e73ce1" />

5. Keep in mind that even if you have a government approved account, you'll be restricted to seeing whatever layers the information's owner has given you permission to.



## Organising your endpoints

You get folders (that you can rename, drag and drop, click, remove, etc), you get profiles for quickloading endpoint presets, you get a search bar and you get favourites to pin to the top of the list.

An honourable mention for multi-layer services.
Because I can't code, I have only managed to be able to display that services DO have multiple layers inside, not what they are.

For this reason, when you load one and see that, you can right click to add a layer from that service. I recommend that you then rename, if you need a reminder of what layers are contained within.
