import shap, joblib, pandas as pd, numpy as np, matplotlib.pyplot as plt

bundle   = joblib.load('thunderstorm_model.pkl')
model    = bundle['model']
FEATURES = bundle['features']

df = pd.read_csv('bengaluru_thunderstorm_features_v2.csv')
X_test = df[df['YEAR'] >= 2023][FEATURES].fillna(0)

print("Computing SHAP values...")
explainer = shap.TreeExplainer(model)
shap_vals = explainer.shap_values(X_test)

shap.summary_plot(shap_vals, X_test, show=False)
plt.tight_layout()
plt.savefig('shap_summary.png', dpi=150, bbox_inches='tight')
plt.close()
print("Saved: shap_summary.png")

importance = pd.Series(np.abs(shap_vals).mean(axis=0), index=FEATURES)
print("\nTop features by SHAP impact:")
imp = importance.sort_values(ascending=False).round(4)
print(imp.to_string())
imp.to_csv('shap_importance.csv')