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

#ifndef MATMUL_RELU_CV_STORY_COMMON_UTILS_H
#define MATMUL_RELU_CV_STORY_COMMON_UTILS_H

#include <cstdio>
#include <fcntl.h>
#include <fstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

#define ERROR_LOG(fmt, args...) fprintf(stdout, "[ERROR]  " fmt "\n", ##args)

inline bool ReadFile(const std::string &filePath, size_t &fileSize,
                     void *buffer, size_t expectedSize) {
  struct stat fileInfo;
  int statResult = stat(filePath.data(), &fileInfo);
  if (statResult == -1) {
    ERROR_LOG("failed to get file %s", filePath.c_str());
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
  if (fileSizeFromStream != expectedSize) {
    ERROR_LOG("file size mismatch. path=%s actual=%zu expected=%zu",
              filePath.c_str(), fileSizeFromStream, expectedSize);
    inputStream.close();
    return false;
  }
  fileBuffer->pubseekpos(0, std::ios::in);
  auto *readBuffer = static_cast<std::filebuf::char_type *>(buffer);
  std::streamsize readCount = fileBuffer->sgetn(
      readBuffer, static_cast<std::streamsize>(fileSizeFromStream));
  inputStream.close();
  if (static_cast<size_t>(readCount) != fileSizeFromStream) {
    ERROR_LOG("short read. path=%s read=%zu expected=%zu", filePath.c_str(),
              static_cast<size_t>(readCount), fileSizeFromStream);
    return false;
  }
  fileSize = fileSizeFromStream;
  return true;
}

inline bool WriteFile(const std::string &filePath, const void *buffer,
                      size_t size) {
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

#endif // MATMUL_RELU_CV_STORY_COMMON_UTILS_H
