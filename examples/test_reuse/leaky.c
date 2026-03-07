#include <stdlib.h>
#include <string.h>


int	main(void)
{
	char	*ptr;

	ptr = malloc(32);
	strcpy(ptr, "first");
	ptr = malloc(64);
	strcpy(ptr, "second");
	free(ptr);
	return (0);
}
