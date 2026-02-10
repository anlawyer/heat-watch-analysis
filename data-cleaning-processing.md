### Data cleaning steps

1. Download data from [OSF API](https://osf.io/user/9neka?tab=2)
- All campains in CAPA/NIHHIS Heat Watch project: 70 folders downloaded 

2. Download data from cities in nested folders
- `Oklahoma City`
    - Parent folder included both "Air Quality Results" and "Heat Results" sub-folders 
- `Mystic River (2021)`
    - Parent folder included both "Air Quality" and "Heat Watch" sub-folders 
- `UHI Assessments (2017, 2018)`
    - These cities contained "All Data" zip files
        - Richmond, VA (2017)
        - Washington, DC (2018)
        - Baltimore, MD (2018)
    - This city contained 3 separate zip files
        - Portland, OR (2017)
- `Heat Watch Campaigns (2019)`
    - Added "Heat Watch" to folder name for each city
    - Some cities included an "All Data" folder
        - Boston, MA
        - Fort Lauderdale, FL
        - Honolulu, HI
        - West Palm Beach, FL
        - Worcester, MA
        - Yonkers, NY
    - Some cities had nested "Traverse Points" and "Surface Models" folders containing "Traverse Points" and "Area-wide" zip files
        - Los Angeles, CA
        - Oakland-Berkeley, CA
        - San Bernadino (CA)
        - Boise, ID
        - Sacramento (CA)
        - Victorville, CA
        - San Juan, PR (though these zip files were not in nested folders)
- `Heat Watch VFIC` (Virginia Foundation for Independent Colleges)
    - Each folder contained two zip files, one for rasters and one for traverses
    - Added "Heat Watch {city}, VA" to folder name for each city
        - Abingdon
        - Arlington
        - Farmville
        - Harrisonburg
        - Lynchburg
        - Petersburg
        - Richmond
            - Also added "(2021)" to this Richmond file as there was an existing "Richmond, VA (2017)" file
        - Salem
        - Virginia Beach
        - Winchester

3. Remove non-US cities (5 folders removed)
- Freetown, Sierra Leone
- Nairobi Kenya 
- Rio de Janeiro
- Santiago Chile
- Vancouver

4. Rename city folders with `&amp;` or other characters besides spaces, hyphens, commas, and parens

<details>
<summary>Renamed cities</summary>
    
- Heat Watch Bronx `&amp;` Manhattan (2021) 
    - Heat Watch Bronx and Manhattan (2021)
- Heat Watch Houston `&amp;` Harris County 
    - Heat Watch Houston-Harris County
- Heat Watch Johnson County `&amp;` Wyandotte County 
    - Heat Watch Johnson County and Wyandotte County
- Heat Watch San Jose `&amp;` Santa Clara
    - Heat Watch San Jose and Santa Clara
- Heat Watch Scranton `&amp;` Wilkes Barre
    - Heat Watch Scranton and Wilkes Barre
- Heat Watch Seattle `&amp;` King County
    - Heat Watch Seattle and King County

</details>

5. Unzip all files
- Most cities' files were unzipped programmatically 
- Some cities had nested zip files (i.e. within a `"All Data_Heat Watch {City}"` zip file), so manually unzipped them

6. Normalize **traverses** file format

- Most common traverse folder name format: `traverses_chw_{city_name}_{ID}`
    - Manually added underscores where missing from file names
    - Created `traverses_chw_{city_name}` folders for some cities with different folder formats (see below)
- Most common traverse filename format:
    - `{time}_trav.shp` (and other shapefile-related extensions: `.dbf`, `.prj`, `.shx`, `.cpg`, `.sbn`, `.sbx`, `.shp.xml`)
        - `time`: am, af, pm (`am` == morning, `af` == afternoon, `pm` == evening)

- Some cities' folders or file formats are slightly different
    - Baltimore, MD
        - Filename format: `trav_{time}`.
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
    - Boise, ID
        - Filename format: `{time}_TraversePoints_Boise` (where `time` is `AM`, `Eve`, `PM`). 
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders, separated for Boise and Nampa.
    - Boston, MA
        - Filename format: `Boston_{time}` (where `time` is `am`, `ev`, `pm_revised`). 
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders. 
    - Bronx and Manhattan (2021)
        - `am` and `pm` trav files have additional timestamp text in filename (`af` files don't have this).
    - Charlotte NC
        - All files have `charlotte-north-carolina_{time}_trav` filename format.
    - Cincinnati
        - Originally had `{time}_trav_rh` and `{time}_trav_temp` folders for each time period. Moved all `temp` files into main `traverses` folder, left `rh` files in separate folder.
    - Houston-Harris County
        - Originally had `{time}_trav_humidity` and `{time}_trav_temp` files within main `traverses` folder. Kept all `temp` files in main `traverses` folder, moved `humidity` files to separate folder.
    - Laredo TX
        - All files have `laredo-texas_{time}_trav` filename format.
    - Los Angeles, CA
        - Filename format: `{time}_trav` (where `time` is `af`, `am`, and `ev`).
        - Oddly named/nested folders, moved to main `traverses` folders for both `north` and `south` groups.
    - Miami
        - **Need to combine / reconcile multiple days of files.**
    - Oakland-Berkeley, CA
        - Filename format: `{time}_TraversePoints_OaklandBerkeley` (where `time` is `Afternoon`, `Evening`, `Morning`). 
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
    - Palo Alto CA
        - All files have `palo-alto-california_{time}_trav` filename format.
    - Pierce County WA
        - All files have `pierce-county-washington_{time}_trav` filename format.
    - Portland, OR (2017)
        - **Need to unzip folders to see what is in them.**
    - Richmond, VA (2017)
        - Filename format: `all_{time}`. 
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
    - Sacramento (CA)
        - Filename format: `{time}_TraversePoints_Sacramento` (where `time` is `Afternoon`, `Evening`, `Morning`). 
    - San Bernadino (CA)
        - Filename format: `{time}_TraversePoints_SanBernadino` (where `time` is `Afternoon`, `Evening`, `Morning`). 
    - San Jose and Santa Clara
        - Originally had `{time}_trav_humid`/`{time}_trav_humidity` and `{time}_trav_temp` files within main `traverses` folder. Kept all `temp` files in main `traverses` folder, moved `humidity` files to separate folder.
    - San Juan, PR
        - Filename format: `{time}_Traverse_Points_San_Juan` (where `time` is `AM`, `Eve`, `PM`).
    - Santa Fe NM
        - All files have `santa-fe-new-mexico_{time}_trav` filename format.
    - Washington, D.C. (2018)
        - Filename format: `trav_{time}`.
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
    - Victorville, CA
        - Filename format: `{time}_TraversePoints_Victorville` (where `time` is `Afternoon`, `Evening`, `Morning`). 
    - Yonkers, NY
        - Filename format: `{time}_trav` (where `time` is `af`, `am`, `ev`).
        - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.

- Cities with folder updates

<details>
<summary>Folder changes, minor edits and notes</summary>

- Austin
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
- Burlington
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
    - Also has `bicycle_traverses` folder: files have `{name}_norm` convention, containing normalized data.
- Detroit 
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
    - Also has `bicycle_traverses` folder, files have `{name}_norm` convention, containing normalized data.
- El Paso
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
    - Also has `bicycle_traverses` folder, files have `{name}_norm` convention, containing normalized data.
- Fort Lauderdale, FL
    - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
- Honolulu, HI
    - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
- Jackson
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
- Las Cruces
    - Originally had `{time}_trav_chw_lascruces_{ID}` and `{time}_rasters_chw_lascruces_{ID}` folders for each time period. Moved files to main `rasters` and `traverses` folders.
    - Also has `bicycle_traversepoints` folder, files have `{name}_norm` convention, containing normalized data.
- Mystic River (2021)
    - Has raster and traverse files for two days of collection: Day 1 has `af`, `am`, and `pm`,  Day 2 only has `am` files.
        - Renamed to remove `day 1` from folder name to be main folder, left `day 2` folder separate.
- New Orleans
    - Two days of collection: 08-08 has `am` only, and 08-18 has `af` and `pm`.
        - Combined into main `traverses` folder. 
    - Also has `bicycle_traverses` folder, files have `{name}_norm` convention, only `af` and `pm` files.
- Rhode Island
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
    - Also includes `mi` files, and `Appendix Data` folder; keeping separate from main folders.
- Roanoke
    - Also has `bicycle_traverses` folder, files have `{name}_norm` convention, only `af` and `pm` files.
- San Francisco
    - Has traverse files for two days of collection: Day 1 has `af`, `am`, and `pm`,  Day 2 only has `pm` files.
        - Renamed to remove `day 1` from folder name to be main folder, left `day 2` folder separate.
- Seattle and King County
    - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
- West Palm Beach, FL
    - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.
- Worcester, MA
    - Oddly named/nested folders, moved to main `rasters` and `traverses` folders.

</details>

7. Normalize **raster** file format
- Most common raster folder name format: `rasters_chw_{city_name}_{ID}`
    - Manually added underscores where missing from file names
- Most common raster filename format:
    - `{time}_hi_{scale}.tif`, `{time}_t_{scale}.tif`
        - `hi` == "heat index"
        - `t` == "temperature" 
        - `scale`: f or c (most files use `f`)
        - `time`: am, af, pm (`am` == morning, `af` == afternoon, `pm` == evening)

- Some cities include "mean" raster files: `mean_hi.tif`, `mean_temp.tif`

<details>
<summary>Cities with "mean" raster files</summary>
    
- Asheville
- Brockton
- Chicago
- Iowa City and Cedar Rapids
- Johnson County and Wyandotte County
- Little Rock
- Longmont
- MetroWest (Framingham MA)
- Oklahoma City
- Salt Lake City
- Scranton and Wilkes Barre
- Sedona
- Toledo

</details>

- Some cities' file format are slightly different

<details>
<summary>Cities with different filename formats</summary>

- `{time}_heat_index_{scale}.tif`
    - Albuquerque
    - Houston-Harris County
    - Fort Lauderdale, FL
    - Jersey City, Newark, and Elizabeth
    - Las Cruces
    - Los Angeles (both north and south files)
    - Roanoke
    - Sacramento (CA)
    - Seattle and King County
    - Spokane
    - West Palm Beach, FL
    - Worcester, MA
- `{time}_htndx_{scale}.tif`
    - Rhode Island
- `{time}_ht_ndx_{scale}.tif`
    - Austin
    - Burlington
    - Cincinnati
    - Detroit
    - El Paso (rasters)
    - Jackson
    - Miami (all days)
    - New Orleans (all days)
    - San Jose and Santa Clara
    - Seattle and King County
- `{city}-{time}_temp_{scale}.tif`
    - Charlotte NC
    - Laredo TX
    - Palo Alto CA
    - Pierce County WA
    - Santa Fe NM 
    - El Paso (bicycle_rasters)
- Other misc raster filename formats 
    - Baltimore, MD
        - Three `bal_{time}.tif` files
    - Boise, ID
        - `AM_Area-wide_Boise.tif`, `Eve...`, `PM...` 
        - `AM_Area-wide_Nampa.tif`, `Eve...`, `PM...`
    - Boston, MA
        - `Afternoon_Area-wide_HeatIndex_Boston.tif`, `Evening...`, `Morning...`
        - `Afternoon_Area-wide_Temperature_Boston.tif`, `Evening...`, `Morning...`
    - Honolulu, HI
        - `Afternoon_Area-wide_HeatIndex_111719.tiff`, `Evening...`, `Morning...`
        - `Afternoon_Area-wide_Temperature_1117191.tif`, `Evening...`, `Morning...`
    - Oakland-Berkeley, CA
        - `Afternoon_Area-wide_OaklandBerkeley.tif`, `Evening...`, `Morning...`
    - Richmond, VA (2017)
        - `rva_af.tif`, `rva_am.tif`, `rva_pm.tif`
    - San Bernardino (CA)
        - `Afternoon_Area-wide_SanBernardino.tif`, `Evening...`, `Morning...`
    -  San Juan, PR
        - `SanJuan_am_HI_F.tif`, `...ev...`, `...pm...`
        - `SanJuan_am_T_F.tif`, `...ev...`, `...pm...`
    - Victorville, CA
        - `Afternoon_Area-wide_Victorville.tif`, `Evening...`, `Morning...`
    - Washington, D.C. (2018)
        - `dc_af.tif`, `dc_am.tif`, `dc_pm.tif`
    - Yonkers, NY
        - `ev_hi_ranger.tif`, `am...`, `af...`
        - `ev_t_f_ranger.tif`, `am...`, `af...`

</details>

8. Document cities with any issues

<details>
<summary>Cities with other **traverse** file issues</summary>

- Missing files
    - Albuquerque (2021)
        - Only `am` and `af` files, no `pm`

</details>

<details>
<summary>Cities with other **raster** file issues</summary>

- Missing files
    - Brooklyn
        - Only `am` and `af` files, no `pm`
    - Albuquerque (2021)
        - Only `am` and `af` files, no `pm`
- Other
    - Seattle and King County
        - Additional set of raster files in OSF: `seattle_king_county_120120`. Only `kingcounty_093020` was included in the `All Data` folder, so only using that one for now. 
    - New Orleans
        - Two days of collection: 08-08 has `am` only, 08-18 has `af` and `pm`
            - Combined into main `rasters` folder. 
    - Miami
        - Data collected on several different days, files from different days have same name
            - 06-27: `am`, `af`, `pm`
            - 07-05: `am` only
            - 07-11: `am` only
            - 07-12: `pm` only
            - 07-19: `af` and `pm`
    - El Paso
        - Has `rasters` and `rasters-extended` folders. 
    - San Francisco
        - 2 days of traverses, only one day of rasters, removed `day 1` text from raster folder name.
    - Los Angeles
        - Has north and south folders/files with same file names for both regions.  
    - Mystic River
        - Has raster and traverse files for two days of collection: Day 1 has `am`, `af`, and `pm`, Day 2 only has `am`.
    - Portland, OR
        - Has 3 zip files that the code wasn't able to unzip, each over 1 GB: `825a_2.zip`, `825b_2.zip`, `825c_2.zip`. 

</details>
