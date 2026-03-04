### File format

The data from all U.S. city campaigns from the CAPA/NIHHIS Heat Watch project is hosted on GMU's Hopper servers. 

Here is the folder and file naming format all the data will follow:

```
/parent-directory/clean/
└── Heat Watch {City Name}, {state postal code abbreviation}
    ├── rasters_chw_{city_name}_{ID?}
        └── {time}_hi_f.tif
        └── {time}_t_f.tif
    └── traverses_chw_{city_name}_{ID?}
        └── {time}_trav.shp
```

Example:
```
Heat Watch Abingdon, VA
├── rasters_chw_abingdon_110121
    └── af_hi_f.tif
    └── af_t_f.tif
└── traverses_chw_abingdon_110121
    └── af_trav.shp
    └── am_trav.shp
    └── pm_trav.shp
```

### Data format

At minimum, within each shapefile for each time period in the `traverses` folder for each city, there should be a `geometry` column that is a POINT with latitude and longitude, and the temperature column should be named `temp_f`. 

Other temperature columns may exist (i.e. `T_C`), along with other columns of data that may have been collected (i.e. `rh`, `ht_ndx`). Not all files will have the variety of data, but the `geometry` and `temp_f` columns are necessary. 
