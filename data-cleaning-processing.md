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
    -   Portland, OR (2017)
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

6. Normalize file format: **traverses**

- Most common traverse folder name format: `traverses_chw_{city_name}_{ID}`
    - Manually added underscores where missing from file names
    - Created `traverses_chw_{city_name}` folders for some cities with different folder formats (see below)
- Most common raster filename format:
    - `{time}_trav.shp` (and other shapefile-related extensions: `.dbf`, `.prj`, `.shx`, `.cpg`, `.sbn`, `.sbx`, `.shp.xml`)
        - `time`: am, af, pm (`am` == morning, `af` == afternoon, `pm` == evening)

- Some cities' folders or file formats are slightly different
    - Austin
        - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
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
    - Burlington
        - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
        - Also has `bicycle_traverses` folder: files have `{name}_norm` convention, containing normalized data.
    - Charlotte NC
        - All files have `charlotte-north-carolina_{time}_trav` filename format.
    - Cincinnati
        - Originally had `{time}_trav_rh` and `{time}_trav_temp` folders for each time period. Moved all `temp` files into main `traverses` folder, left `rh` files nested.
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
    - Houston-Harris County
        - Has `{time}_trav_humidity` and `{time}_trav_temp` files within main `traverses` folder.
    - Jackson
        - Originally had `{time}_trav` folders for each time period. Moved all trav files into main `traverses` folder.
    - Laredo TX
        - All files have `laredo-texas_{time}_trav` filename format.
    - Las Cruces
        - Originally had `{time}_trav_chw_lascruces_{ID}`  and `{time}_rasters_chw_lascruces_{ID}` folders for each time period. Moved files to main `rasters` and `traverses` folders.
        - Also has `bicycle_traversepoints` folder, files have `{name}_norm` convention, containing normalized data.
    - Los Angeles, CA
        - Oddly named/nested folders, moved to main `traverses` folders for both `north` and `south` groups.
        - Filenames include `time` as `af`, `am`, and `ev`. 
        - Traverse folders originally said `processed-traverse...` and raster folders originally said `surface models...`.

7. Normalize file format: **rasters**
- Most common raster folder name format: `rasters_chw_{city_name}_{ID}`
    - Manually added underscores where missing from file names
- Most common raster file name format:
    - `{time}_hi_{scale}.tif`, `{time}_t_{scale}.tif`
        - `hi` == "heat index"
        - `t` == "temperature" 
        - `scale`: f or c (most files use `f`)
        - `time`: am, af, pm (`am` == morning, `af` == afternoon, `pm` == evening)

- Some cities include "mean" raster files: `mean_hi.tif`, `mean_temp.tif`

<details>
<summary>Cities with "mean" files</summary>
    
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
<summary>Cities with different formats</summary>

- `{time}_heat_index_{scale}.tif`
    - Albuquerque
    - Houston-Harris County
    - Fort Lauderdale, FL
        - Unzipped files are nested in "All Data" folder -- need to re-org into `rasters` and `traverses` folders, but otherwise file names are consistent with others
    - Jersey City, Newark, and Elizabeth
    - Las Cruces
    - Los Angeles (both north and south files)
    - Roanoke
    - Sacramento (CA)
    - Seattle and King County
    - Spokane
    - West Palm Beach, FL
        - Unzipped files are nested in "All Data" folder -- need to re-org into `rasters` and `traverses` folders, but otherwise file names are consistent with others
    - Worcester, MA
        - Unzipped files are nested in "All Data" folder -- need to re-org into `rasters` and `traverses` folders, but otherwise file names are consistent with others
- `{time}_htndx_{scale}.tif`
    - Rhode Island
- `{time}_ht_ndx_{scale}.tif`
    - Austin
    - Burlington
    - Cincinnati
    - Detroit
    - El Paso
    - Jackson
    - Miami (all days)
    - New Orleans (all days)
    - San Jose and Santa Clara
- `{city}-{time}_temp_{scale}.tif`
    - Charlotte NC
    - Laredo TX
    - Palo Alto CA
    - Pierce County WA
    - Santa Fe NM 
- Other misc files formats 
    - Baltimore, MD
        - `Heat Watch Baltimore, MD (2018) / All Data_Baltimore / baltimore /`
            - Three `bal_{time}.tif` files
    - Boise, ID
        - `Heat Watch Boise, ID / Area-wide_Boise / Area-wide_Boise /`
            - `AM_Area-wide_Boise.tif`, `Eve...`, `PM...` 
            - `AM_Area-wide_Nampa.tif`, `Eve...`, `PM...`
    - Boston, MA
        - `Heat Watch Boston, MA / All Data_Boston_Heat Watch 2019 / All Data_Boston_Heat Watch 2019 /`
            - `Afternoon_Area-wide_HeatIndex_Boston.tif`, `Evening...`, `Morning...`
            - `Afternoon_Area-wide_Temperature_Boston.tif`, `Evening...`, `Morning...`
    - Honolulu, HI
        - `Heat Watch Honolulu, HI / All Data_Honolulu_Heat Watch 2019 / All Data_Honolulu_Heat Watch 2019 /`
            - `Afternoon_Area-wide_HeatIndex_111719.tiff`, `Evening...`, `Morning...`
            - `Afternoon_Area-wide_Temperature_1117191.tif`, `Evening...`, `Morning...`
    - Oakland-Berkeley, CA
        - `Heat Watch Honolulu, HI / Area-wide_OaklandBerkeley /`
            - `Afternoon_Area-wide_OaklandBerkeley.tif`, `Evening...`, `Morning...`
    - Richmond, VA (2017)
        - Heat Watch Richmond, VA (2017) / All Data_Richmond / rva / temperature surfaces /
            - `rva_af.tif`, `rva_am.tif`, `rva_pm.tif`
    - San Bernardino (CA)
        - Heat Watch San Bernardino (CA) / Area-wide_SanBernardino /
            - `Afternoon_Area-wide_SanBernardino.tif`, `Evening...`, `Morning...`
    -  San Juan, PR
        - Heat Watch San Juan, PR / Area-Wide_San Juan /
            - `SanJuan_am_HI_F.tif`, `...ev...`, `...pm...`
            - `SanJuan_am_T_F.tif`, `...ev...`, `...pm...`
    - Victorville, CA
        - Heat Watch Victorville, CA / Area-wide_Victorville /
            - `Afternoon_Area-wide_Victorville.tif`, `Evening...`, `Morning...`
    - Washington, D.C. (2018)
        - Heat Watch Washington, D.C. (2018) / All Data_Washington DC / dc / temperature surfaces /
            - `dc_af.tif`, `dc_am.tif`, `dc_pm.tif`
    - Yonkers, NY
        - `Heat Watch Yonkers, NY / All Data_Yonkers_Heat Watch 2019 / All Data_Yonkers_Heat Watch 2019 /`
            - `ev_hi_ranger.tif`, `am...`, `af...`
            - `ev_t_f_ranger.tif`, `am...`, `af...`
            - Also, unzipped files are nested in "All Data" folder -- need to re-org into `rasters` and `traverses` folders

</details>

8. Document cities with any issues

<details>
<summary>Cities with other traverse file issues</summary>

- Missing files
    - Albuquerque (2021)
        - Only `am` and `af` files, no `pm`

</details>

<details>
<summary>Cities with other raster file issues</summary>

- Missing files
    - Brooklyn
        - Only `am` and `af` files, no `pm`
    - Albuquerque (2021)
        - Only `am` and `af` files, no `pm`
- Other
    - Seattle and King County
        - Two sets of raster files: `seattle_king_county_120120` and `kingcounty_093020`
            - Currently only unzipped / using `seattle_king_county_120120`
    - Rhode Island
        - Also includes `mi` files in addition to `am`, `af`, and `pm`, but ReadMe doesn't say what it is
        - Also has "Appendix Data" zip files, not unzipping those
    - New Orleans
        - Two days of collection:
            - 08-08 has `am` only
            - 08-18 has `af` and `pm`
        - Combined into one rasters folder
    - Miami
        - Data collected on several different days, files from different days have same name
            - 06-27: `am`, `af`, `pm`
            - 07-05: `am` only
            - 07-11: `am` only
            - 07-12: `pm` only
            - 07-19: `af` and `pm`
    - El Paso
        - Has `rasters` folder, `rasters-extended` folder, and `bicycle_rasters` folder
            - `rasters`: `ht_ndx` `t` 
            - `rasters-extended`: `hi` `t` `ranger`
            - `bicycle_rasters`: `hi` `temp` `scaled`
    - San Francisco
        - 2 days of traverses, only one day of rasters 
            - Day 1 has `am`, `af`, and `pm`
            - Day 2 only has `pm`
    - Los Angeles
        - Has north and south folders/files with same file names for both regions 
    - Mystic River
        - Has raster and traverse files for two days of collection
            - Day 1 has `am`, `af`, and `pm`
            - Day 2 only has `am`
    - Portland, OR
        - Has 3 zip files that the code wasn't able to unzip, each over 1 GB: `825a_2.zip`, `825b_2.zip`, `825c_2.zip`

</details>
