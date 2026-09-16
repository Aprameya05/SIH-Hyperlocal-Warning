import pandas as pd
from sklearn.metrics import roc_auc_score, confusion_matrix

df = pd.read_csv('bengaluru_thunderstorm_features.csv')
test = df[df['YEAR'] >= 2023].copy()

# We'll plug real CAPE/K values when Atul delivers
# For now simulate with what we have as a structural test
# CAPE proxy: high DTR + high RF = convective day
test['era5_rule'] = ((test['DTR'] > 10) & (test['RF'] > 1)).astype(int)

from sklearn.metrics import classification_report
print(classification_report(test['LABEL'], test['era5_rule']))