### Data cleaning steps

1. Download data from (OSF API)[https://osf.io/user/9neka?tab=2]
- All campains in CAPA/NIHHIS Heat Watch project: 70 folders downloaded 

2. Download data from cities in nested folders
- Oklahoma City
    - Parent folder included both "Air Quality Results" and "Heat Results" sub-folders 
- Mystic River (2021)
    - Parent folder included both "Air Quality" and "Heat Watch" sub-folders 
        - Heat Watch folder includes raster and traverse files for two days of collection
- UHI Assessments (2017, 2018)
    - These cities contained "All Data" zip files
        - Richmond, VA (2017)
        - Washington, DC (2018)
        - Baltimore, MD (2018)
    - This city contained 3 separate zip files
    -   Portland, OR (2017)
- Heat Watch Campaigns (2019)
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
- Heat Watch VFIC (Virginia Foundation for Independent Colleges)
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
