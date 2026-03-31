import os
import sys
import time
import logging
import traceback
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def _check_dependencies():
    missing = []
    try:
        import fitz
    except ImportError:
        missing.append('PyMuPDF (pip install PyMuPDF)')
    try:
        import pytesseract
    except ImportError:
        missing.append('pytesseract (pip install pytesseract)')
    try:
        from PIL import Image
    except ImportError:
        missing.append('Pillow (pip install Pillow)')
    try:
        import numpy
    except ImportError:
        missing.append('numpy (pip install numpy)')
    try:
        import tqdm
    except ImportError:
        missing.append('tqdm (pip install tqdm)')
    return missing


def _check_cuda():
    try:
        import torch
        return torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except ImportError:
        return False, None


def _get_gpu_memory_usage():
    try:
        import torch
        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated(0) / (1024 * 1024)
            reserved = torch.cuda.memory_reserved(0) / (1024 * 1024)
            return allocated, reserved
    except Exception:
        pass
    return 0.0, 0.0


def _clear_gpu_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _extract_page_image(pdf_doc, page_number, dpi=300):
    page = pdf_doc[page_number - 1]
    mat = None
    try:
        import fitz
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        from PIL import Image
        import numpy as np
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = Image.fromarray(img_array, 'RGBA').convert('RGB')
        else:
            img = Image.fromarray(img_array, 'RGB')
        return img
    except Exception as e:
        raise RuntimeError(f"Failed to extract page {page_number} as image: {e}")


def _run_ocr_on_image(img, languages, output_format, use_gpu, gpu_memory_limit):
    import pytesseract
    lang_str = '+'.join(languages)
    results = {}
    try:
        if use_gpu:
            allocated, _ = _get_gpu_memory_usage()
            if allocated > gpu_memory_limit * 0.9:
                logger.warning("GPU memory near limit, clearing cache before OCR")
                _clear_gpu_cache()
        if output_format in ('text', 'both'):
            text = pytesseract.image_to_string(img, lang=lang_str)
            results['text'] = text
        if output_format in ('hocr', 'both'):
            hocr = pytesseract.image_to_pdf_or_hocr(img, lang=lang_str, extension='hocr')
            results['hocr'] = hocr
    except Exception as e:
        raise RuntimeError(f"OCR processing failed: {e}")
    return results


def _save_results(results, base_name, page_number, output_dir, output_format):
    saved_files = []
    if output_format in ('text', 'both'):
        txt_filename = f"{base_name}_page_{page_number}.txt"
        txt_path = os.path.join(output_dir, txt_filename)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(results.get('text', ''))
        saved_files.append(txt_path)
        logger.debug(f"Saved text output: {txt_path}")
    if output_format in ('hocr', 'both'):
        hocr_filename = f"{base_name}_page_{page_number}.hocr"
        hocr_path = os.path.join(output_dir, hocr_filename)
        hocr_data = results.get('hocr', b'')
        if isinstance(hocr_data, bytes):
            with open(hocr_path, 'wb') as f:
                f.write(hocr_data)
        else:
            with open(hocr_path, 'w', encoding='utf-8') as f:
                f.write(hocr_data)
        saved_files.append(hocr_path)
        logger.debug(f"Saved HOCR output: {hocr_path}")
    return saved_files


def run(input_data: dict) -> dict:
    start_time = time.time()

    # --- Extract and validate inputs ---
    pdf_path = input_data.get('pdf_path', '')
    output_dir = input_data.get('output_dir', os.getcwd())
    start_page = input_data.get('start_page', 1)
    end_page = input_data.get('end_page', None)
    languages = input_data.get('languages', ['eng'])
    output_format = input_data.get('format', 'text').lower()
    dpi = input_data.get('dpi', 300)
    batch_size = input_data.get('batch_size', 5)
    gpu_memory_limit = input_data.get('gpu_memory_limit', 4096)

    output_files = []
    processed_pages = []
    failed_pages = {}
    total_pages = 0

    # --- Check dependencies ---
    missing_deps = _check_dependencies()
    if missing_deps:
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': 0,
            'processing_time': time.time() - start_time,
            'error': f"Missing dependencies: {', '.join(missing_deps)}"
        }

    # --- Import after dependency check ---
    try:
        import fitz
        import pytesseract
        from PIL import Image
        import numpy as np
        from tqdm import tqdm
    except ImportError as e:
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': 0,
            'processing_time': time.time() - start_time,
            'error': f"Import error: {e}"
        }

    # --- Validate pdf_path ---
    if not pdf_path:
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': 0,
            'processing_time': time.time() - start_time,
            'error': "pdf_path is required"
        }

    if not os.path.isfile(pdf_path):
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': 0,
            'processing_time': time.time() - start_time,
            'error': f"PDF file not found: {pdf_path}"
        }

    # --- Validate format ---
    if output_format not in ('text', 'hocr', 'both'):
        logger.warning(f"Invalid format '{output_format}', defaulting to 'text'")
        output_format = 'text'

    # --- Validate DPI ---
    if not isinstance(dpi, int) or dpi < 72:
        logger.warning(f"Invalid DPI {dpi}, defaulting to 300")
        dpi = 300

    # --- Create output directory ---
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': 0,
            'processing_time': time.time() - start_time,
            'error': f"Cannot create output directory '{output_dir}': {e}"
        }

    # --- Check CUDA availability ---
    use_gpu, gpu_name = _check_cuda()
    if use_gpu:
        logger.info(f"CUDA available. GPU: {gpu_name}")
    else:
        logger.info("CUDA not available. Falling back to CPU processing.")

    # --- Derive base name for output files ---
    base_name = Path(pdf_path).stem

    # --- Open PDF ---
    try:
        pdf_doc = fitz.open(pdf_path)
        total_pages = len(pdf_doc)
        logger.info(f"Opened PDF '{pdf_path}' with {total_pages} pages.")
    except Exception as e:
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': 0,
            'processing_time': time.time() - start_time,
            'error': f"Failed to open PDF: {e}"
        }

    # --- Validate page range ---
    if start_page < 1:
        logger.warning(f"start_page {start_page} < 1, setting to 1")
        start_page = 1
    if start_page > total_pages:
        pdf_doc.close()
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': total_pages,
            'processing_time': time.time() - start_time,
            'error': f"start_page ({start_page}) exceeds total pages ({total_pages})"
        }
    if end_page is None or end_page > total_pages:
        end_page = total_pages
    if end_page < start_page:
        pdf_doc.close()
        return {
            'status': 'failed',
            'output_files': [],
            'processed_pages': [],
            'failed_pages': {},
            'total_pages': total_pages,
            'processing_time': time.time() - start_time,
            'error': f"end_page ({end_page}) is less than start_page ({start_page})"
        }

    pages_to_process = list(range(start_page, end_page + 1))
    logger.info(f"Processing pages {start_page} to {end_page} (total: {len(pages_to_process)}).")

    # --- Batch processing loop ---
    try:
        with tqdm(total=len(pages_to_process), desc="OCR Processing", unit="page") as pbar:
            for batch_start in range(0, len(pages_to_process), batch_size):
                batch = pages_to_process[batch_start:batch_start + batch_size]

                for page_number in batch:
                    try:
                        # Check GPU memory
                        if use_gpu:
                            allocated, reserved = _get_gpu_memory_usage()
                            if allocated > gpu_memory_limit * 0.85:
                                logger.warning(
                                    f"GPU memory usage high ({allocated:.1f}MB allocated). "
                                    f"Clearing cache."
                                )
                                _clear_gpu_cache()

                        # Extract page image
                        img = _extract_page_image(pdf_doc, page_number, dpi=dpi)

                        # Run OCR
                        ocr_results = _run_ocr_on_image(
                            img,
                            languages=languages,
                            output_format=output_format,
                            use_gpu=use_gpu,
                            gpu_memory_limit=gpu_memory_limit
                        )

                        # Save results
                        saved = _save_results(
                            ocr_results,
                            base_name=base_name,
                            page_number=page_number,
                            output_dir=output_dir,
                            output_format=output_format
                        )
                        output_files.extend(saved)
                        processed_pages.append(page_number)
                        logger.info(f"Page {page_number} processed successfully.")

                    except Exception as page_err:
                        err_msg = traceback.format_exc()
                        logger.error(f"Failed to process page {page_number}: {page_err}\n{err_msg}")
                        failed_pages[str(page_number)] = str(page_err)

                    finally:
                        # Cleanup GPU tensors per page
                        _clear_gpu_cache()
                        pbar.update(1)

                # Batch-level GPU cache clear
                _clear_gpu_cache()

    except Exception as loop_err:
        logger.error(f"Unexpected error in processing loop: {loop_err}\n{traceback.format_exc()}")
    finally:
        try:
            pdf_doc.close()
        except Exception:
            pass

    # --- Final GPU cleanup ---
    _clear_gpu_cache()

    processing_time = time.time() - start_time
    status = 'success' if processed_pages else 'failed'

    logger.info(
        f"Processing complete. Status: {status}. "
        f"Pages processed: {len(processed_pages)}/{len(pages_to_process)}. "
        f"Time: {processing_time:.2f}s."
    )

    return {
        'status': status,
        'output_files': output_files,
        'processed_pages': processed_pages,
        'failed_pages': failed_pages,
        'total_pages': total_pages,
        'processing_time': round(processing_time, 4)
    }