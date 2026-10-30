# UHI Mapping and Heat Watch Analysis

## UHI Mapping Campaign Data

Use the [notebook](./process-campaign-data/process-campaign-data.ipynb) to run the processing [scripts](./process-campaign-data/process_data.py) for your campaign data:

1. Clean the data, removing temperature freezes and spikes due to potential sensor errors.

2. Combine any number of individual traverse data files into three time periods, based on the time windows you set.

3. Convert csv files into shapefiles.

Use the [notebook](./rf-model-pipeline/run-model.ipynb) to run the random forest model on your traverse dataset to generate city-wide air temperature maps. See [documentation](./rf-model-pipeline/pipeline-documentation.md) for more details.

Use the scripts in this [folder](./uhi-analysis-scripts) to generate additional plots and maps for further analysis of your data. Based on this [repo](https://github.com/luisortizuriarte/uhi-campaign-tools/tree/main).

## NOAA Heat Watch Data

Data compiled from campaigns run by [NOAA](https://heat.gov/urban-heat-islands-mapping-campaign-program/) and [CAPA Strategies](https://www.capastrategies.com/heat-watch) in the U.S., 2017 - 2024.

### Data Access, Cleaning, Normalizing, Validating Steps

1. Download and unzip files from OSF via steps in [this file](./noaa-heat-watch-data/data-access-api.md).

2. Run a script to validate the format of the folder and file names, like [this one](./noaa-heat-watch-data/format-validation-scripts.md).
   - Document inconsistencies and outliers in the folder and file formats in [this file](./noaa-heat-watch-data/data-outlier-record.md).
   - Normalize the outliers to the folder and file name [format](./noaa-heat-watch-data/data-format.md) using scripts like [these](./data-normalizing-scripts.md).

3. Run a script to validate the format of the data itself, like [this one](./noaa-heat-watch-data/format-validation-scripts.md).
   - Normalize the outliers to the data [format](./noaa-heat-watch-data/data-format.md) using scripts like [these](./noaa-heat-watch-data/data-normalizing-scripts.md).
