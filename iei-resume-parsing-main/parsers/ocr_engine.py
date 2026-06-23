import os
import sys

# Cache readers to avoid reloading model weights on each call
_PADDLE_OCR_INSTANCE = None
_EASY_OCR_INSTANCE = None

def run_ocr(image_path, logger=None):
    """
    Runs OCR on the given image file.
    Tries PaddleOCR first, then EasyOCR, then PyTesseract.
    """
    global _PADDLE_OCR_INSTANCE, _EASY_OCR_INSTANCE
    
    if logger:
        logger.info(f"Running OCR on: {os.path.basename(image_path)}")
    
    # 1. Try PaddleOCR
    try:
        if logger:
            logger.info("Attempting PaddleOCR...")
        # Import dynamically to prevent crashes if not installed
        from paddleocr import PaddleOCR
        import logging
        # Suppress paddle logs
        logging.getLogger("ppocr").setLevel(logging.WARNING)
        
        if _PADDLE_OCR_INSTANCE is None:
            _PADDLE_OCR_INSTANCE = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
            
        result = _PADDLE_OCR_INSTANCE.ocr(image_path, cls=True)
        if result and len(result) > 0 and result[0] is not None:
            text_lines = []
            for line in result[0]:
                text_lines.append(line[1][0])
            ocr_text = "\n".join(text_lines)
            if ocr_text.strip():
                if logger:
                    logger.info("OCR successfully completed using PaddleOCR.")
                return ocr_text
    except Exception as e:
        if logger:
            logger.warning(f"PaddleOCR failed or not installed: {e}")
            
    # 2. Try EasyOCR
    try:
        if logger:
            logger.info("Attempting EasyOCR as fallback...")
        import easyocr
        if _EASY_OCR_INSTANCE is None:
            # Silence EasyOCR weights download messages if possible, or run normally
            _EASY_OCR_INSTANCE = easyocr.Reader(['en'], gpu=False) # CPU preferred for stability in background
            
        result = _EASY_OCR_INSTANCE.readtext(image_path)
        if result:
            ocr_text = "\n".join([item[1] for item in result])
            if ocr_text.strip():
                if logger:
                    logger.info("OCR successfully completed using EasyOCR.")
                return ocr_text
    except Exception as e:
        if logger:
            logger.warning(f"EasyOCR failed or not installed: {e}")
            
    # 3. Try PyTesseract
    try:
        if logger:
            logger.info("Attempting PyTesseract as fallback...")
        import pytesseract
        from PIL import Image
        
        ocr_text = pytesseract.image_to_string(Image.open(image_path))
        if ocr_text.strip():
            if logger:
                logger.info("OCR successfully completed using PyTesseract.")
            return ocr_text
    except Exception as e:
        if logger:
            logger.error(f"PyTesseract failed or not installed: {e}")
            
    if logger:
        logger.error("All OCR engines failed or were not available.")
    return ""
