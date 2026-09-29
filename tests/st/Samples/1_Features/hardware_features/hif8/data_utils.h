/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the License);
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an AS IS BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*!
 * \file data_utils.h
 * \brief
 */

#ifndef DATA_UTILS_H
#define DATA_UTILS_H
#include <fcntl.h>
#include <fstream>
#include <sys/stat.h>
#include <unistd.h>

#define ERROR_LOG(fmt, args...) fprintf(stdout, "[ERROR]  " fmt "\n", ##args)

bool ReadFile(const std::string &filePath, size_t &fileSize, void *buffer,
              size_t bufferSize) {
  struct stat fileInfo;
  int statResult = stat(filePath.data(), &fileInfo);
  if (statResult == -1) {
    ERROR_LOG("failed to get file");
    return false;
  }
  if (S_ISREG(fileInfo.st_mode) == 0) {
    ERROR_LOG("%s is not a file, please enter a file", filePath.c_str());
    return false;
  }

  std::ifstream inputStream;
  inputStream.open(filePath, std::ios::binary);
  if (!inputStream.is_open()) {
    ERROR_LOG("Open file failed. path = %s", filePath.c_str());
    return false;
  }

  std::filebuf *fileBuffer = inputStream.rdbuf();
  size_t fileSizeFromStream =
      fileBuffer->pubseekoff(0, std::ios::end, std::ios::in);
  if (fileSizeFromStream == 0) {
    ERROR_LOG("file size is 0");
    inputStream.close();
    return false;
  }
  if (fileSizeFromStream > bufferSize) {
    ERROR_LOG("file size is larger than buffer size");
    inputStream.close();
    return false;
  }
  fileBuffer->pubseekpos(0, std::ios::in);
  auto *readBuffer = static_cast<std::filebuf::char_type *>(buffer);
  fileBuffer->sgetn(readBuffer,
                    static_cast<std::streamsize>(fileSizeFromStream));
  fileSize = fileSizeFromStream;
  inputStream.close();
  return true;
}

/**
 * @brief Write data to file
 * @param [in] filePath: file path
 * @param [in] buffer: data to write to file
 * @param [in] size: size to write
 * @return write result
 */
bool WriteFile(const std::string &filePath, const void *buffer, size_t size) {
  if (buffer == nullptr) {
    ERROR_LOG("Write file failed. buffer is nullptr");
    return false;
  }

  int fd =
      open(filePath.c_str(), O_RDWR | O_CREAT | O_TRUNC, S_IRUSR | S_IWRITE);
  if (fd < 0) {
    ERROR_LOG("Open file failed. path = %s", filePath.c_str());
    return false;
  }

  size_t writeSize = write(fd, buffer, size);
  (void)close(fd);
  if (writeSize != size) {
    ERROR_LOG("Write file Failed.");
    return false;
  }

  return true;
}
#endif // DATA_UTILS_H
