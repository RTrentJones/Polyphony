/**
 * File Upload Component
 */

'use client'

import { useRef, useState, useCallback } from 'react'
import { Upload, File, X } from 'lucide-react'
import { cn, formatFileSize } from '@/lib/utils'

interface FileUploadProps {
  onFileSelect: (file: File) => void
  accept?: string
  maxSize?: number // in bytes
  label?: string
  helperText?: string
  error?: string
}

export default function FileUpload({
  onFileSelect,
  accept = '.txt,.doc,.docx,.pdf',
  maxSize = 10 * 1024 * 1024, // 10MB default
  label,
  helperText,
  error,
}: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)

  const validateFile = (file: File): string | null => {
    if (maxSize && file.size > maxSize) {
      return `File size must be less than ${formatFileSize(maxSize)}`
    }

    if (accept) {
      const acceptedTypes = accept.split(',').map((t) => t.trim())
      const fileExtension = `.${file.name.split('.').pop()?.toLowerCase()}`
      const isValidType = acceptedTypes.some(
        (type) =>
          type === fileExtension ||
          file.type === type ||
          (type.endsWith('/*') && file.type.startsWith(type.replace('/*', '')))
      )

      if (!isValidType) {
        return `File type not accepted. Accepted types: ${accept}`
      }
    }

    return null
  }

  const handleFileChange = (file: File) => {
    const validationError = validateFile(file)

    if (validationError) {
      setFileError(validationError)
      setSelectedFile(null)
      return
    }

    setFileError(null)
    setSelectedFile(file)
    onFileSelect(file)
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      handleFileChange(file)
    }
  }

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    const file = e.dataTransfer.files?.[0]
    if (file) {
      handleFileChange(file)
    }
  }, [])

  const handleClearFile = () => {
    setSelectedFile(null)
    setFileError(null)
    if (inputRef.current) {
      inputRef.current.value = ''
    }
  }

  const displayError = error || fileError

  return (
    <div className="w-full">
      {label && (
        <label className="block text-sm font-medium text-gray-700 mb-2">
          {label}
        </label>
      )}

      <div
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={cn(
          'relative border-2 border-dashed rounded-lg p-6 cursor-pointer transition-all',
          dragActive
            ? 'border-primary-500 bg-primary-50'
            : displayError
            ? 'border-red-300 bg-red-50'
            : 'border-gray-300 hover:border-primary-400 hover:bg-gray-50'
        )}
      >
        <input
          ref={inputRef}
          type="file"
          onChange={handleInputChange}
          accept={accept}
          className="hidden"
        />

        {selectedFile ? (
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-primary-100 rounded-lg">
                <File className="h-5 w-5 text-primary-600" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-900">
                  {selectedFile.name}
                </p>
                <p className="text-xs text-gray-500">
                  {formatFileSize(selectedFile.size)}
                </p>
              </div>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleClearFile()
              }}
              className="p-1 hover:bg-gray-200 rounded transition-colors"
            >
              <X className="h-4 w-4 text-gray-500" />
            </button>
          </div>
        ) : (
          <div className="text-center">
            <Upload
              className={cn(
                'mx-auto h-12 w-12 mb-3',
                displayError ? 'text-red-400' : 'text-gray-400'
              )}
            />
            <p className="text-sm font-medium text-gray-900 mb-1">
              Click to upload or drag and drop
            </p>
            <p className="text-xs text-gray-500">
              {accept.replace(/\./g, '').toUpperCase()} up to{' '}
              {formatFileSize(maxSize)}
            </p>
          </div>
        )}
      </div>

      {displayError && (
        <p className="mt-1 text-sm text-red-600">{displayError}</p>
      )}

      {helperText && !displayError && (
        <p className="mt-1 text-sm text-gray-500">{helperText}</p>
      )}
    </div>
  )
}
