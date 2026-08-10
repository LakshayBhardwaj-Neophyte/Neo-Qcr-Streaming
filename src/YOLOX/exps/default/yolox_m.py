# exps/example/custom/my_yoloxm.py

from yolox.exp import Exp as MyExp

class Exp(MyExp):
    def __init__(self):
        super().__init__()

        # YOLOX-M
        self.depth = 0.67
        self.width = 0.75

        self.num_classes = 4

        self.data_dir = "/mnt/storage/data/interns_data/yolox_training/YOLOX/datasets/COCO"

        self.train_ann = "instances_train2017.json"
        self.val_ann = "instances_val2017.json"

        # Image size
        self.input_size = (640, 640)
        self.test_size = (640, 640)

        # Training
        self.max_epoch = 150
        self.data_num_workers = 8