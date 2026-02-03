### Data cleaning steps

1. Download data from [OSF API](https://osf.io/user/9neka?tab=2)
- All campains in CAPA/NIHHIS Heat Watch project: 70 folders downloaded 

2. Download data from cities in nested folders
- `Oklahoma City`
    - Parent folder included both "Air Quality Results" and "Heat Results" sub-folders 
- `Mystic River (2021)`
    - Parent folder included both "Air Quality" and "Heat Watch" sub-folders 
        - Heat Watch folder includes raster and traverse files for two days of collection
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

4. Rename city folders with &amp; or other characters besides spaces, hyphens, commas, and parens
- Heat Watch Bronx &amp; Manhattan (2021) 
    - Heat Watch Bronx and Manhattan (2021)
- Heat Watch Houston &amp; Harris County 
    - Heat Watch Houston-Harris County
- Heat Watch Johnson County &amp; Wyandotte County 
    - Heat Watch Johnson County and Wyandotte County
- Heat Watch San Jose &amp; Santa Clara
    - Heat Watch San Jose and Santa Clara
- Heat Watch Scranton &amp; Wilkes Barre
    - Heat Watch Scranton and Wilkes Barre
- Heat Watch Seattle &amp; King County
    - Heat Watch Seattle and King County

5. Unzip all files
- Most cities' files were unzipped programmatically 
- Some cities had nested zip files (i.e. within a `"All Data_Heat Watch {City}"` zip file), so manually unzipped these:
    - Austin 
    - Burlington
    - Cincinnati
    - Detroit
    - El Paso
    - Houston-Harris County 
    - Jackson 
    - Las Cruces 
    - Miami
    - New Orleans
    - Rhode Island
    - Roanoke 
    - San Jose and Santa Clara
    - Seattle and King County

6. Normalize file format
- Most common raster folder name format: `rasters_chw_{city_name}_{ID}`
    - Manually added underscores where missing from file names
- Most common raster file name format:
    - `{time}_hi_{scale}.tif` (`hi` == "heat index" data)
    - `{time}_t_{scale}.tif` (`t` == "temperature" data)
        - `scale`: f or c (most files use `f`)
        - `time`: am, af, pm (`am` == morning, `af` == afternoon, `pm` == evening)
- Some cities include "mean" files as well: `mean_hi.tif`, `mean_temp.tif`
    - Asheville
    - Brockton
    - Chicago 
- Some cities' file format are slightly different
    - `{time}_heat_index_{scale}.tif`
        - Albuquerque
    - `{time}_htndx_{scale}.tif`
        - Rhode Island
    - `{time}_ht_ndx_{scale}.tif`
        - Austin
        - San Jose and Santa Clara
        - Burlington
        - Cincinnati
    - `{city}-{time}_temp_{scale}.tif`
        - Charlotte NC 
- Other misc files formats 
    - Baltimore
        - `Heat Watch Baltimore, MD (2018) / All Data_Baltimore / baltimore /`
            - Three `bal_{time}.tif` files
    - Boise
        - `Heat Watch Boise, ID / Area-wide_Boise / Area-wide_Boise /`
            - `AM_Area-wide_Boise.tif`, `Eve_Area-wide_Boise.tif`, `PM_Area-wide_Boise.tif` 
            - `AM_Area-wide_Nampa.tif`, `Eve_Area-wide-Nampa.tif`, `PM_Area-wide_Nampa.tif`
    - Boston
        - `Heat Watch Boston, MA / All Data_Boston_Heat Watch 2019 / All Data_Boston_Heat Watch 2019 /`
            - `Afternoon_Area-wide_HeatIndex_Boston.tif`, `Evening_Area-wide_HeatIndex_Boston.tif`, `Morning_Area-wide_HeatIndex_Boston.tif`
            - `Afternoon_Area-wide_Temperature_Boston.tif`, `Evening_Area-wide_Temperature_Boston.tif`, `Morning_Area-wide_Temperature_Boston.tif`

7. Document cities with any issues
- Missing files
    - Brooklyn
        - Only `am` and `af` files, no `pm`
    - Seattle and King County
        - Two sets of raster files: seattle_king_county_120120 and kingcounty_093020
    - Rhode Island
        - Also includes `mi` files in addition to `am`, `af`, and `pm`, but ReadMe doesn't say what it is
        - Also has "Appendix Data" zip files, not unzipping those
    - New Orleans
        - Two days of collection: 08-08 has mornings only, 08-18 has afternoons and evenings
        - Combined into one rasters folder
    - Miami
        - Data collected on several different days, files from different days have same name 
