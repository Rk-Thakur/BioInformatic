import pandas as pd

df = pd.read_csv("erepo.tabbed.txt", sep="\t")
print(df.columns)  # check the exact column names first

genes = ["GCK", "HNF1A", "HNF4A"]
diabetes = df[df["HGNC Gene Symbol"].isin(genes)]
diabetes.to_csv("clingen_diabetes.csv", index=False)
print(len(diabetes), "diabetes variants saved")
