from src.data_handler.mongo import Mongo
db = Mongo()
db.set_tenant("reliance")
doc = db.metadata.find_one({"metadata_id": "45779220260820171355stream"})
print("DOC in DB:", doc)
