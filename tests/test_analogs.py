import pandas as pd
df = pd.read_csv('data/bengaluru_thunderstorm_features_merged.csv', parse_dates=['date'])
print(df.columns.tolist()[:10])
print(df.shape)
print('LABEL' in df.columns, 'CAPE' in df.columns, 'K_INDEX' in df.columns)