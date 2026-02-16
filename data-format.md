### Data format

The data from all 85 U.S. city campaigns from the CAPA/NIHHIS Heat Watch project is now hosted on GMU's Hopper servers. 

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
```
