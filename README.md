# Heat Watch Analysis

This repository is for tracking my progress as I process and analyze data from the NOAA Heat Watch campaigns. 

### Data Access, Cleaning, Normalizing, Validating Steps

1. Download and unzip files from OSF via steps in [this file](./data-access-api.md).

2. Run a script to validate the format of the folder and file names, like [this one](./format-validation-scripts.md). 
    - Document inconsistencies and outliers in the folder and file formats in [this file](./data-outlier-record.md).
    - Normalize the outliers to the folder and file name [format](./data-format.md) using scripts like [these](./data-normalizing-scripts.md).

3. Run a script to validate the format of the data itself, like [this one](./format-validation-scripts.md).
    - Normalize the outliers to the data [format](./data-format.md) using scripts like [these](./data-normalizing-scripts.md).
