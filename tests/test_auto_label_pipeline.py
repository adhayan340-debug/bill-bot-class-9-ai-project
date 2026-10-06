import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

import app as app_module
from app import ensure_training_dataset_layout, save_yolo_annotation_file


class AutoLabelPipelineTests(unittest.TestCase):
    def test_ensure_training_dataset_layout_creates_expected_folders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ensure_training_dataset_layout(root)

            self.assertTrue((root / 'images' / 'train').is_dir())
            self.assertTrue((root / 'images' / 'val').is_dir())
            self.assertTrue((root / 'labels' / 'train').is_dir())
            self.assertTrue((root / 'labels' / 'val').is_dir())

    def test_save_yolo_annotation_file_writes_normalized_boxes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_path = root / 'labels' / 'train' / 'sample.txt'
            save_yolo_annotation_file(
                out_path,
                image_width=100,
                image_height=200,
                boxes=[(0, 50, 100, 80, 150)],
            )

            contents = out_path.read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(len(contents), 1)
            self.assertEqual(contents[0].split(), ['0', '0.650000', '0.625000', '0.300000', '0.250000'])


class LocalAdminLoginTests(unittest.TestCase):
    def test_local_login_fails_when_password_is_not_configured(self):
        with patch.dict(app_module.ADMIN_USERS, {}, clear=True):
            response = app_module.app.test_client().post('/admin/login/local', data={
                'username': 'Adhayan Das',
                'password': 'unconfigured-test-password',
            })

        self.assertEqual(response.status_code, 401)


class DatasetApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.prices_path = self.root / 'prices.json'
        self.prices_path.write_text(json.dumps({'Bottle': 2.5}), encoding='utf-8')
        self.base_dir_patch = patch.object(app_module, 'BASE_DIR', self.root)
        self.prices_patch = patch.object(app_module, 'PRICES_PATH', self.prices_path)
        self.raw_dir_patch = patch.object(app_module, 'RAW_DIR', self.root / 'dataset' / 'raw')
        self.labelled_dir_patch = patch.object(app_module, 'LABELLED_DIR', self.root / 'dataset' / 'labelled')
        self.base_dir_patch.start()
        self.prices_patch.start()
        self.raw_dir_patch.start()
        self.labelled_dir_patch.start()
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session['admin_user'] = 'test-admin'

    def tearDown(self):
        self.labelled_dir_patch.stop()
        self.raw_dir_patch.stop()
        self.prices_patch.stop()
        self.base_dir_patch.stop()
        self.tempdir.cleanup()

    def write_image(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(cv2.imwrite(str(path), np.zeros((100, 200, 3), dtype=np.uint8)))

    def test_manual_annotation_saves_normalized_box_and_delete_removes_pair(self):
        image_path = self.root / 'dataset' / 'images' / 'train' / 'sample.jpg'
        label_path = self.root / 'dataset' / 'labels' / 'train' / 'sample.txt'
        self.write_image(image_path)

        response = self.client.post('/api/dataset/annotations', json={
            'split': 'train',
            'filename': 'sample.jpg',
            'boxes': [{'class_id': 0, 'x1': 20, 'y1': 10, 'x2': 100, 'y2': 70}],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(label_path.read_text(encoding='utf-8').strip().split(), ['0', '0.300000', '0.400000', '0.400000', '0.600000'])
        annotation_response = self.client.get('/api/dataset/annotations/train/sample.jpg')
        self.assertEqual(annotation_response.status_code, 200)
        self.assertEqual(annotation_response.json['boxes'][0]['class_id'], 0)
        delete_response = self.client.delete('/api/dataset/train/sample.jpg')
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(image_path.exists())
        self.assertFalse(label_path.exists())

    def test_upload_and_raw_import_create_empty_label_files(self):
        encoded_ok, encoded = cv2.imencode('.jpg', np.zeros((20, 30, 3), dtype=np.uint8))
        self.assertTrue(encoded_ok)
        upload_response = self.client.post('/api/dataset/upload', data={
            'split': 'train',
            'files': (io.BytesIO(encoded.tobytes()), 'new.jpg'),
        }, content_type='multipart/form-data')
        self.assertEqual(upload_response.status_code, 200)
        self.assertTrue((self.root / 'dataset' / 'labels' / 'train' / 'new.txt').is_file())

        raw_path = self.root / 'dataset' / 'raw' / 'capture.jpg'
        self.write_image(raw_path)
        import_response = self.client.post('/api/dataset/import-raw', json={'filename': 'capture.jpg', 'split': 'val'})
        self.assertEqual(import_response.status_code, 200)
        self.assertTrue((self.root / 'dataset' / 'images' / 'val' / 'capture.jpg').is_file())
        self.assertTrue((self.root / 'dataset' / 'labels' / 'val' / 'capture.txt').is_file())
        delete_response = self.client.delete('/api/dataset/raw/capture.jpg')
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(raw_path.exists())

    def test_train_page_renders_dataset_editor(self):
        response = self.client.get('/train')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="annotation-canvas"', response.data)
        self.assertIn(b'id="dataset-upload-button"', response.data)

    def test_ai_miss_does_not_create_full_frame_annotation(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        result = SimpleNamespace(boxes=None, plot=lambda: frame)
        with patch.object(app_module.camera, 'get_frame', return_value=(frame, 1)), patch.object(app_module, 'YOLO') as yolo:
            yolo.return_value.return_value = [result]
            response = self.client.post('/api/capture-label', json={'product': 'Bottle'})

        self.assertEqual(response.status_code, 200)
        label_path = self.root / response.json['label_file']
        self.assertEqual(label_path.read_text(encoding='utf-8'), '')


if __name__ == '__main__':
    unittest.main()
